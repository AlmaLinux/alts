from alts_app import app


@app.task(bind=True, ignore_result=True)
def test_task(self, *args, **kwargs):
    print(self.__dict__)
    print('User args')
    print(args)
    print('User kwargs')
    print(kwargs)
