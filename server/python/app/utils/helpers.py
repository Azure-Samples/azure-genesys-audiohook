def process_parameters(parameters: dict) -> dict:
    """
    Process parameters dictionary to convert any Pydantic models to dictionaries.

    This function recursively converts Pydantic models found in parameter dictionaries
    to their dictionary representation using model_dump(). This is particularly useful
    when preparing message parameters for JSON serialization in the AudioHook protocol.

    Args:
        parameters: Dictionary that may contain Pydantic models or lists of Pydantic models

    Returns:
        Dictionary with all Pydantic models converted to dictionaries suitable for JSON serialization

    Example:
        >>> from pydantic import BaseModel
        >>> class TestModel(BaseModel):
        ...     name: str
        ...     value: int
        >>> params = {"model": TestModel(name="test", value=123), "other": "value"}
        >>> process_parameters(params)
        {'model': {'name': 'test', 'value': 123}, 'other': 'value'}
    """
    processed_parameters = {}
    for key, value in parameters.items():
        if hasattr(value, "model_dump"):
            # If it's a single Pydantic model
            processed_parameters[key] = value.model_dump()
        elif isinstance(value, list) and value and hasattr(value[0], "model_dump"):
            # If it's a list of Pydantic models
            processed_parameters[key] = [
                item.model_dump() if hasattr(item, "model_dump") else item
                for item in value
            ]
        else:
            # Regular value
            processed_parameters[key] = value
    return processed_parameters
