import json
import logging
import os

LOG = logging.getLogger(__name__)
LOG.setLevel(logging.DEBUG)


def build_return_json(code, body):
    return {
        "statusCode": code,
        "body": json.dumps(body, indent=2),
        "headers": {"Content-Type": "application/json"},
    }


def dict_prepend(original, key, value):
    # Since Python 3.7, python dictionaries preserve insertion
    # order, so to prepend an item to the top of the dictionary,
    # we create a new dictionary inserting the new key first,
    # then add the previous output
    new_dict = {key: value}
    new_dict.update(original)
    return new_dict


def get_os_var(varnam):
    try:
        return os.environ[varnam]
    except KeyError as exc:
        raise Exception(f"The environment variable '{varnam}' must be set")


def parse_codes(codes):
    data = []
    if codes:
        data = codes.split(",")
    return data


def _param_str(params, param):
    # Default is the empty string
    if params and param in params:
        return params[param]
    return ""


def _param_bool(params, param):
    # Default is True
    if params and param in params:
        if params[param].lower() not in ["false", "no", "off"]:
            return True
    return False


def _param_int(params, param):
    # Default is 0
    number = 0
    if params and param in params:
        try:
            number = int(params[param])
        except ValueError as exc:
            msg = f"The parameter '{param}' must be an integer"
            raise ValueError(msg) from exc
    return number


def _param_hide_inactive_bool(params):
    # Default is to hide inactive codes
    return not _param_bool(params, "show_inactive_codes")


def _param_show_other_bool(params):
    # Default is to hide "other" code
    return _param_bool(params, "show_other_code")


def _param_show_no_program_bool(params):
    # Default is to show "no program" code
    return not _param_bool(params, "hide_no_program_code")


def _param_date_str(params):
    # Default is to show "no program" code
    return _param_str(params, "target_date")


def _param_limit_int(params):
    # Default is 0 (no limit)
    limit = _param_int(params, "limit")
    if limit < 0:
        raise ValueError("The parameter 'limit' must be a positive Integer")
    return limit


def _param_priority_list(params):
    if params and "priority_codes" in params:
        return parse_codes(params["priority_codes"])

    return None


def params_dict(event):
    """
    Parse query-string parameters from the trigger event.

    Parameters
    ----------
        event: dict
            The event passed to the lambda handler by the execution
            environment.

    Returns
    -------
    dict
        Dictionary of configuration parameters parsed from the trigger event.
    """
    _params = {}
    if "queryStringParameters" in event:
        _params = event["queryStringParameters"]
        LOG.debug(f"Query-string _parameters: {_params}")
    params = {
        "hide_inactive": _param_hide_inactive_bool(_params),
        "limit": _param_limit_int(_params),
        "priority_codes": _param_priority_list(_params),
        "show_no_program": _param_show_no_program_bool(_params),
        "show_other": _param_show_other_bool(_params),
        "date": _param_date_str(_params),
    }
    return params
