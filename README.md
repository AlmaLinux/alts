Instruments and libraries required for system to run in current state:

- Terraform >= 0.13;
- Ansible (current version is 2.9);
- RabbitMQ;
- Celery >= 5.0; 
- Gevent;
- Docker.

Celery launch command example:
```bash
RABBITMQ_USER=test-system RABBITMQ_PASSWORD=$YOUR_PASSWORD celery -A alts_app worker --pool=gevent --concurrency=10 --loglevel=DEBUG
```

System overview
--
AlmaLinux test system is designed to be fast, scalable and easy maintainable solution
for end-to-end packages testing.