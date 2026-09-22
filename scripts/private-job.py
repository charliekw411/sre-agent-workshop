"""Run inside the workshop VNet. Credentials never leave this process or reach logs."""

import base64
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


class JobError(Exception):
    pass


class RequestError(JobError):
    def __init__(self, status):
        self.status = status
        super().__init__(f"Request failed with HTTP {status}; response body withheld.")


class NetworkError(JobError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise JobError("Redirect refused for an authenticated request.")


def required(key):
    value = os.environ.get(key)
    if not value:
        raise JobError(f"Missing job setting {key}. Start this job through the workshop helper.")
    return value


def endpoint(value, suffix):
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(suffix)
            or parsed.username or parsed.password or parsed.port or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment):
        raise JobError("Unexpected service endpoint.")
    return value.rstrip("/")


def request_json(method, url, headers=None, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        url, data=data, headers=headers or {}, method=method,
    )
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RequestError(error.code) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise NetworkError("Network request failed; check private connectivity and DNS.") from None


def vault_access_token():
    identity_endpoint = required("IDENTITY_ENDPOINT")
    parsed = urllib.parse.urlsplit(identity_endpoint)
    if (parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise JobError("Unexpected managed-identity endpoint.")
    client_id = str(uuid.UUID(required("IDENTITY_CLIENT_ID")))
    query = urllib.parse.urlencode({
        "resource": "https://vault.azure.net",
        "api-version": "2019-08-01",
        "client_id": client_id,
    })
    response = request_json("GET", identity_endpoint + "?" + query, {
        "X-IDENTITY-HEADER": required("IDENTITY_HEADER"),
    })
    token = response.get("access_token")
    if not isinstance(token, str) or not token:
        raise JobError("Managed identity did not return an access token.")
    return token


def fault_secret(initialize=False):
    vault = endpoint(required("KEY_VAULT_URI"), ".vault.azure.net")
    url = vault + "/secrets/fault-token?api-version=7.4"
    timeout = 600 if initialize else 120
    deadline = time.monotonic() + timeout
    for attempt in range(60):
        try:
            headers = {
                "Authorization": "Bearer " + vault_access_token(),
                "Content-Type": "application/json",
            }
            try:
                response = request_json("GET", url, headers)
            except RequestError as error:
                if error.status != 404:
                    raise
                if not initialize:
                    raise JobError("Fault credential is not initialized; finish azd up.") from None
                response = request_json("PUT", url, headers, {"value": secrets.token_hex(32)})
            value = response.get("value")
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{48,128}", value):
                raise JobError("Fault credential has an unexpected format; existing secrets were not replaced.")
            return value
        except RequestError as error:
            if error.status not in {403, 408, 429, 500, 502, 503, 504}:
                raise
        except NetworkError:
            pass
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        print(f"Waiting for private Key Vault connectivity or identity permissions ({attempt + 1})...", flush=True)
        time.sleep(min(10, remaining))
    raise JobError("Private Key Vault access timed out; check private DNS, endpoint approval, and managed-identity RBAC.")


def invoke_fault(request):
    if not isinstance(request, dict) or set(request) != {"path", "parameters"}:
        raise JobError("Unexpected fault request.")
    path, parameters = request["path"], request["parameters"]
    if (not isinstance(path, str) or path not in {"cpu", "errors", "storage", "storage/release", "reset", "status"}
            or not isinstance(parameters, dict)
            or any(type(value) is not int for value in parameters.values())):
        raise JobError("Unexpected fault action or arguments.")
    url = endpoint(required("ORDERS_API_URL"), ".azurecontainerapps.io")
    token = fault_secret()
    result = request_json(
        "GET" if path == "status" else "POST", url + "/fault/" + path,
        {"X-Fault-Token": token, "Content-Type": "application/json"},
        None if path == "status" else parameters,
    )
    if not isinstance(result, dict) or token in json.dumps(result):
        raise JobError("Unexpected fault response; output withheld.")
    return result


def main():
    request_id = required("WORKSHOP_REQUEST_ID")
    if not re.fullmatch(r"[a-f0-9]{32}", request_id):
        raise JobError("Unexpected request identifier.")
    operation = required("WORKSHOP_OPERATION")
    if operation == "initialize":
        fault_secret(initialize=True)
        result = {"initialized": True}
    elif operation == "fault":
        request = json.loads(base64.b64decode(required("WORKSHOP_REQUEST"), validate=True))
        result = invoke_fault(request)
    else:
        raise JobError("Unknown private job operation.")
    print(f"WORKSHOP_RESULT:{request_id}:" + json.dumps(result, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    try:
        main()
    except JobError as error:
        print(f"ERROR: {error}", file=sys.stderr, flush=True)
        sys.exit(1)
    except (KeyError, ValueError, TypeError, AttributeError):
        print("ERROR: Unexpected job configuration or service response; details withheld.", file=sys.stderr, flush=True)
        sys.exit(1)
