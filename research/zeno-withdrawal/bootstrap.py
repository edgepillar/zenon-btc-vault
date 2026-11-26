"""Wait for the disposable devnet to pass its height-10 transport switch."""

import json
import re
import subprocess
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener


RPC_LIMIT = 64 * 1024
DEVNET_CHAIN_ID = 69
PROJECT = "zeno-devnet"
OPENER = build_opener(ProxyHandler({}))


def frontier(port):
    request = Request(
        f"http://127.0.0.1:{port}",
        data=b'{"jsonrpc":"2.0","id":1,"method":"ledger.getFrontierMomentum","params":[]}',
        headers={"Content-Type": "application/json"},
    )
    try:
        with OPENER.open(request, timeout=4) as response:
            raw = response.read(RPC_LIMIT + 1)
    except (URLError, TimeoutError):
        return None
    if len(raw) > RPC_LIMIT:
        raise RuntimeError("Devnet bootstrap RPC exceeded its byte limit")
    result = json.loads(raw).get("result")
    if result is None:
        return None
    if result["chainIdentifier"] != DEVNET_CHAIN_ID:
        raise RuntimeError("Devnet bootstrap RPC returned an unexpected chain")
    return result


def wait_for_transport_switch():
    deadline = time.monotonic() + 240
    while True:
        pillar, rpc = frontier(35991), frontier(35997)
        pillar_height = pillar["height"] if pillar else None
        rpc_height = rpc["height"] if rpc else None
        if (
            pillar_height is not None and pillar_height >= 11
            and rpc_height is not None and rpc_height >= 11
            and abs(time.time() - rpc["timestamp"]) <= 60
        ):
            print(f"Devnet transport ready at producer {pillar_height} and RPC {rpc_height}", flush=True)
            return True, pillar_height
        if time.monotonic() >= deadline:
            print(f"Devnet transport pending at producer {pillar_height} and RPC {rpc_height}", flush=True)
            return False, pillar_height
        time.sleep(5)


def restart_bootstrap_services(services):
    containers = []
    for service in services:
        container = subprocess.check_output(
            [
                "docker", "ps", "--all", "--quiet",
                "--filter", f"label=com.docker.compose.project={PROJECT}",
                "--filter", f"label=com.docker.compose.service={service}",
            ],
            text=True, timeout=10,
        ).strip()
        if re.fullmatch(r"[a-f0-9]{12,64}", container) is None:
            raise RuntimeError(f"Expected one isolated bootstrap container for {service}")
        containers.append(container)
    print(f"Restarting the isolated bootstrap services once ({', '.join(services)})", flush=True)
    subprocess.run(["docker", "restart", "--timeout", "10", *containers], check=True, timeout=75)


if __name__ == "__main__":
    ready, producer_height = wait_for_transport_switch()
    if not ready:
        services = ("rpc",) if producer_height and producer_height >= 11 else (
            "pillar", "pillar2", "pillar3", "observer", "rpc",
        )
        restart_bootstrap_services(services)
        ready, _ = wait_for_transport_switch()
    if not ready:
        raise SystemExit("The isolated devnet did not pass the transport switch")
