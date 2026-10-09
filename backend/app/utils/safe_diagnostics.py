"""Schema-owned paths and fixed error categories; no submitted/provider values."""
from typing import get_args
from pydantic_core import ErrorType


def validation_details(errors, schema):
    names = {'response'}

    def visit(node):
        if isinstance(node, dict):
            properties = node.get('properties', {})
            if isinstance(properties, dict):
                names.update(properties)
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(schema)
    known_codes = set(get_args(ErrorType))
    return {'error_count':len(errors), 'validation_errors':[
        {'location':[part if (isinstance(part, str) and part in names)
                     else ('<index>' if type(part) is int else '<field>')
                     for part in error.get('loc', ())[:12]],
         'code':error.get('type') if error.get('type') in known_codes else 'validation_error'}
        for error in errors[:20]]}


def protocol_details(exc):
    code = getattr(exc, 'code', None)
    if type(code) is not int or not -(2**31) <= code < 2**31:
        code = None
    categories = {-32700:'protocol_parse_error', -32600:'invalid_protocol_request',
                  -32601:'protocol_method_not_found', -32602:'invalid_protocol_parameters',
                  -32603:'protocol_internal_error'}
    # Inspect only exception types, never SDK text/arguments. Wrapped timeouts
    # otherwise appear as a generic RuntimeError and hide the failing stage.
    chain, current = [], exc
    for _ in range(6):
        if current is None or id(current) in {id(item) for item in chain}:
            break
        chain.append(current)
        current = current.__cause__ or current.__context__
    reason = categories.get(code, 'protocol_or_transport_failure')
    if any(isinstance(item, TimeoutError) or type(item).__name__ in
           {'ReadTimeout', 'ConnectTimeout', 'WriteTimeout', 'PoolTimeout', 'TimeoutException'} for item in chain):
        reason = 'timeout'
    return {'protocol_error_code':code, 'reason':reason}
