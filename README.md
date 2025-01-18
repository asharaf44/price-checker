# Price Checker

A Python package that allows you check prices daily on certain websites

## Local Docker Testing

### Build the image

`docker build -t price-checker .`

### Run the container

`docker run -p 9000:8080 price-checker`

### In another terminal, test the function

`curl -XPOST "http://localhost:9000/2015-03-31/functions/function/invocations" -d '{}'`
