from price_checker.app import handler

def test_handler():
    # Mock event and context
    event = {}
    context = {}
    
    # Call handler
    response = handler(event, context)
    
    # Assert response
    assert response["statusCode"] == 200
    assert "Price check completed" in response["body"]