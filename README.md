Instruments and libraries required for system to run in current state:

- Terraform >= 0.13;
- Ansible (current version is 2.9);
- RabbitMQ;
- Celery >= 5.0;
- SQLite;
- Docker (for development purposes);

Celery launch command example:
```shell
AWS_S3_ACCESS_KEY_ID=$KEY_ID AWS_S3_BUCKET=$S3_BUCkET \
AWS_S3_SECRET_ACCESS_KEY=$SECRET_KEY RABBITMQ_USER=test-system \
RABBITMQ_PASSWORD=$YOUR_PASSWORD celery -A alts.app \
worker --pool=threads --concurrency=10 --loglevel=DEBUG
```

To launch scheduler, apply the following command:
```shell
AWS_S3_ACCESS_KEY_ID=$KEY_ID AWS_S3_BUCKET=$S3_BUCkET \
AWS_S3_SECRET_ACCESS_KEY=$SECRET_KEY RABBITMQ_USER=test-system \
RABBITMQ_PASSWORD=$YOUR_PASSWORD uvicorn scheduler.app:app
```

For testing purposes you can add `--reload` argument to scheduler launch 
command, this will enable live code reload.

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

Unresolved issues
--
- Cannot make background task check from web app (reports disabled result backend);
- JWT authorization is required;
- Provision for production server is required;
- Only supports Docker containers;
- No basic tests;
