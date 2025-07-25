import os


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


def _param_bool(params, param):
    if params and param in params:
        if params[param].lower() not in ["false", "no", "off"]:
            return True
    return False


def _param_int(params, param):
    number = 0
    if params and param in params:
        try:
            number = int(params[param])
        except ValueError as exc:
            msg = f"The parameter '{param}' must be an integer"
            raise ValueError(msg) from exc
    return number


def param_inactive_bool(params):
    return not _param_bool(params, "show_inactive_codes")


def param_other_bool(params):
    return _param_bool(params, "show_other_code")


def param_no_program_bool(params):
    return not _param_bool(params, "hide_no_program_code")


def param_limit_int(params):
    limit = _param_int(params, "limit")
    if limit < 0:
        raise ValueError("The parameter 'limit' must be a positive Integer")
    return limit


def param_priority_list(params):
    if params and "priority_codes" in params:
        return parse_codes(params["priority_codes"])

    return None
