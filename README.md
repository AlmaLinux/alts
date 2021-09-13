System overview
--

AlmaLinux Test System (ALTS) - is a way to test rpm/deb packages under realistic circumstances, on real systems with installation, launching, integrity checks, etc. If needed, it also supports third-party test scripts.

AlmaLinux Test System is designed to be a fast, scalable and easily maintainable solution for end-to-end packages testing. 

The system consists of API and task manager. API accepts a request for testing a package and creates a task on it. The manager picks up as many tasks as possible. 

The process of picking up a created task is:
- Starting up a clean environment like Docker or OpenNebula with its initial configurations;
- An attempt to install a package to the system;
- The package integrity check

The system contains several parts:

- RabbitMQ as messaging broker.
- Celery as a task managing environment, using threads pool. Internal data goes through Azure.
- Scheduler web application.
- FastAPI is used as a framework for the API
- Terraform is used as a tool for managing test environments. It allows quickly adding support for new types of environments.

Mentioned tools and libraries are required for ALTS to run in current state:

- Terraform >= 0.13;
- Ansible (current version is 2.9);
- RabbitMQ;
- Celery >= 5.1;
- SQLite;
- Docker (for development purposes);


Test system flow
--

- Web application receives a POST request with the requirements (test runner type, distribution name, version, architecture, package name, package version, additional repositories);
- Application calculates where the task should go (RabbitMQ queue) and applies the task to it;
- Celery worker that listens to the queue receives the task parameters and starts processing;
- Task executes all needed steps and saves artifacts to Azure/S3;
- Celery will save the task result (summary on the run) to the separate Azure/S3 folder;
- Web application can return task status and result by calling a separate endpoint.

Celery launch command example:

`celery -A alts.app worker --pool=threads --concurrency=10 --loglevel=DEBUG`

To launch scheduler, apply the following command:

`uvicorn scheduler.app:app`

For testing purposes, you can add the `--reload` argument to the scheduler launch command, which will enable live code to reload.

Both Celery worker and scheduler REST API services need YAML-based configs to function. Config examples are provided in the `configs` folder.


Filling options in the config file
--

Here is the description of what is necessary to fill in at alts_config.yaml:

```
rabbitqm_host: 'rabbitmq' # hostname for the message broker
rabbitmq_port: 5672 # unprotected broker port
rabbitmq_ssl_port: 5671 # protected broker port. They are used separetly depending on the flag 'use_ssl'
rabbitmq_user: 'test-system' # the user as message broker for connection
rabbitmq_password: # the user's password
rabbitmq_vhost: 'test_system' # message "base" on broker
```

Choosing a backend in the`results backend` option, you define which parameter S3 or Azure you need:
`result_backend: 's3' #  'azureblockblob://$connection_string' # a type of results backend - S3 or connection string to Azure`

Now a group of S3 options for authorization on S3:

```
s3_access_key_id:
s3_secret_access_key:
s3_bucket:
s3_base_path: 'celery_result_backend/' # path at S3 where to put artefacts
s3_region:
s3_endpoint_url: # optional, if not S3 is used, but its substitute
result_backend: 's3'
```

Options to connect to Azure:

```
azureblockblob_container_name: # container name for Celery on Azure
azureblockblob_base_path: # a directory to store testing results
azure_connection_string: # connection string to Azure
azure_logs_container: # container name for logs
```

Running using docker-compose
--

You can start the whole system using the Docker Compose tool.

Pre-requisites:

- `docker` and `docker-compose` tools are installed and set up;

Few preparations needed before you start:

- Open `docker-compose.yml` and set some password in `RABBITMQ_DEFAULT_PASS` variable;
- Copy `configs/example_config.yaml` config file to `configs/alts_config.yaml`;
- Generate JWT secret: `openssl rand -hex 32`
- Fill config with Azure/AWS credentials, JWT secret and RabbitMQ password;

To start the system, run the following command: `docker-compose up -d`. This command will pull RabbitMQ image, build docker images for Celery and Scheduler containers and start the containers themselves. To rebuild images after your local changes, just run `docker-compose up -d --build`.


Running in virtualenv
--
You can start the application inside virtualenv. Create virtualenv:

`python3 -m venv venv`

Activate virtualenv:

`source venv/bin/activate`

Install all needed packages:

`pip install -r requirements/scheduler.txt`

After that, you can start Celery and scheduler.


Scheduling tasks
--

Scheduler part provides 2 REST API endpoints:

- `POST /tasks/schedule` - to schedule package for installation/tests run;
- `GET /tasks/{task_id}/result` - to get result of the task;

As scheduler uses FastAPI, it has Swagger support built-in. You can open Swagger documentation just by following the http://localhost:8000/docs link.

Authentication is achieved using JWT tokens. For ease of testing, there is a `generate_jwt_token.py` script. You can either specify a config file to parse the JWT secret and hashing algorithm or provide them via a parameter. For details on all script parameters, run python `generate_jwt_token.py --help`. Usage example with config:

`python generate_jwt_token.py -c configs/alts_config.yaml -e some@email.com`

With command line parameters:

`python generate_jwt_token.py -s my_very_secret_phrase -a HS256 -e some@email.com`

After acquiring the token, you can put it in Authorize section on the http://localhost:8000/docs page. It will open endpoints for further usage.

`/tasks/schedule` endpoint accepts the following payload:

```
{
  "runner_type": "string", # the instance backend to use (docker, opennebula, etc.). For now only `docker` and `any` values are supported
  "dist_name": "string", # name of distribution you want to test on (debian, ubuntu, centos, etc.)
  "dist_version": "string", # distribution version (20.04, 8, etc.)
  "dist_arch": "string", # CPU architecture you want to test package for. Supported values - 'x86_64', 'i686', 'amd64', 'aarch64', 'arm64'
  "repositories": [], # a list of repositories to add before attempting package installation. Each repository is a dictionary with `name` and `url` values. name` is optional
  "package_name": "string", # the name of the package to install
  "package_version": "string" # optional, version of the package you want to install
}
```
  
`/tasks/{task_id}/result` endpoint returns the result of task. `task_id` - task ID string.

Unresolved issues
--
- Provision for production server is required;
- Only supports Docker containers;
- No basic tests;
