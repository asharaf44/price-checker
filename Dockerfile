FROM public.ecr.aws/lambda/python:3.11

# Copy your full project to the container
COPY . ${LAMBDA_TASK_ROOT}

# Install the package and its dependencies
RUN pip install -e .

# Copy source directory explicitly to ensure it's in the right place
COPY src/price_checker ${LAMBDA_TASK_ROOT}/price_checker/

# Set the handler
CMD [ "price_checker.app.handler" ]