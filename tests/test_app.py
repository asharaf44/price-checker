from price_checker.app import app


def _event(method="POST", path="/api", body=None):
    return {
        "version": "2.0",
        "rawPath": path,
        "rawQueryString": "",
        "requestContext": {"stage": "$default", "http": {"method": method, "path": path}},
        "headers": {},
        "queryStringParameters": {},
        "body": body,
        "isBase64Encoded": False,
    }


def test_rejects_blank_ticker():
    response = app.resolve(_event(body='{"ticker": "!!!"}'), None)
    assert response["statusCode"] == 400


def test_unknown_route_404():
    response = app.resolve(_event(method="GET", path="/nope"), None)
    assert response["statusCode"] == 404
