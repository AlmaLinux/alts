Instruments and libraries required for system to run in current state:

- Terraform >= 0.13;
- Ansible (current version is 2.9);
- RabbitMQ;
- Celery >= 5.0;
- SQLite;
- Docker (for development purposes);

System overview
--
AlmaLinux test system is designed to be fast, scalable and easy maintainable solution
for end-to-end packages testing.

The system contains several parts:

- RabbitMQ as messaging broker;
- Celery as task execution environment, using threads pool;
- Scheduler web application.

Test system flow
--

- Web application receives a POST request with the requirements 
  (test runner type, distribution name, version, architecture, 
  package name, package version, additional repositories);
- Application calculates where the task should go (RabbitMQ queue) 
  and applies the task to it;
- Celery worker that listens to the queue receives the task parameters 
  and starts processing;
- Task executes all needed steps and saves artifacts to S3;
- Celery will save the task result (summary on the run) to the separate S3 folder;
- Web application can return task status and result by calling a separate endpoint.


Celery launch command example:
```shell
celery -A alts.app worker --pool=threads --concurrency=10 --loglevel=DEBUG
```

To launch scheduler, apply the following command:
```shell
uvicorn scheduler.app:app
```

For testing purposes you can add `--reload` argument to scheduler launch 
command, this will enable live code reload.

Both Celery worker and scheduler REST API services need YAML-based configs to function.
Config examples are provided in `configs` folder.


Running using docker-compose
--

You can start the whole system using Docker Compose tool.

Pre-requisites:

- `docker` and `docker-compose` tools are installed and set up;

Few preparations needed before you start:
- Open `docker-compose.yml` and set some password in `RABBITMQ_DEFAULT_PASS` variable;
- Copy `configs/example_config.yaml` config file to `configs/alts_config.yaml`;
- Generate JWT secret: `openssl rand -hex 32`
- Fill  config with AWS credentials, JWT secret and RabbitMQ password;


To start the system, run the following command: `docker-compose up -d`
This will pull RabbitMQ image, build docker images for Celery and Scheduler containers 
and start the containers itself.
To rebuild images after your local changes, just run `docker-compose up -d --build`.


Running in virtualenv
--
You can start the application inside virtualenv.
Create virtualenv:
```shell
python3 -m venv venv
```
Activate virtualenv
```shell
source venv/bin/activate
```
Install all needed packages:
```shell
pip install -r requirements/scheduler.txt
```

After that you can start Celery and scheduler.


Scheduling tasks
--

Scheduler part provides 2 REST API endpoints:

- `POST /tasks/schedule` - to schedule package for installation/tests run;
- `GET /tasks/{task_id}/result` - to get result of the task;

As scheduler uses FastAPI, it has Swagger support built-in. You can open Swagger documentation 
just by following http://localhost:8000/docs link.

Authentication is achieved using JWT tokens. 
For ease of testing there is `generate_jwt_token.py` script. You can specify eiter config file 
to parse JWT secret and hashing algorithm from or provide them via parameter. For details on
all script parameters, run `python generate_jwt_token.py --help`.
Usage example with config:
```shell
python generate_jwt_token.py -c configs/alts_config.yaml -e some@email.com
```
With command line parameters:
```shell
python generate_jwt_token.py -s my_very_secret_phrase -a HS256 -e some@email.com
```

After acquiring the token, you can put it in `Authorize` section on http://localhost:8000/docs page.
This will open endpoints for further usage.

`/tasks/schedule` endpoint accepts the following payload:
```json
{
  "runner_type": "string",
  "dist_name": "string",
  "dist_version": "string",
  "dist_arch": "string",
  "repositories": [],
  "package_name": "string",
  "package_version": "string"
}
```
`runner_type` - the instance backend to use (docker, opennebula, etc.). 
For now only `docker` and `any` values are supported;
`dist_name` - name of distribution you want to test on (debian, ubuntu, centos, etc.);
`dist_version` - distribution version (20.04, 8, etc.);
`dist_arch` - CPU architecture you want to test package for. 
Supported values - 'x86_64', 'i686', 'amd64', 'aarch64', 'arm64';
`repositories` - a list of repositories to add before attempting package installation. 
Each repository is a dictionary with `name` and `url` values. `name` is optional;
`package_name` - the name of the package to install;
`package_version` - optional, version of the package you want to install.


`/tasks/{task_id}/result` endpoint returns the result of task.
`task_id` - task ID string.

Unresolved issues
--
- Provision for production server is required;
- Only supports Docker containers;
- No basic tests;
