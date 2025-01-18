FROM public.ecr.aws/lambda/python:3.11

COPY . ${LAMBDA_TASK_ROOT}

# Install the package and its dependencies
RUN pip install -e .

CMD [ "price_checker.app.handler" ]