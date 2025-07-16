import os


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


def _param_bool(params, param):
    if params and param in params:
        if params[param].lower() not in ["false", "no", "off"]:
            return True
    return False


def param_inactive_bool(params):
    return not _param_bool(params, "show_inactive_codes")


def param_other_bool(params):
    return _param_bool(params, "show_other_code")


def param_no_program_bool(params):
    return not _param_bool(params, "hide_no_program_code")


def param_limit_int(params):
    if params and "limit" in params:
        try:
            return int(params["limit"])
        except ValueError as exc:
            err_str = "QueryStringParameter 'limit' must be an Integer"
            raise ValueError(err_str) from exc
    return 0


def param_priority_list(params):
    if params and "priority_codes" in params:
        return parse_codes(params["priority_codes"])

    return None
