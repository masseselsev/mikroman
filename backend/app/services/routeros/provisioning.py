"""RouterOS objects that have to be *created* to host a container.

The rest of the client reads what is there; this module writes what is missing
so MikroMan can prepare a router to run a container - including itself.

Two rules shape every method here, and they are the reason it is a separate
module rather than a handful of calls at the call sites:

* **A failed command is an exception, never an empty list.** The read methods in
  the sibling mixins answer ``[]`` when a menu is unavailable, because a missing
  package is a normal state to render. Provisioning is different: silently
  swallowing "this bridge already exists with someone else's settings" would let
  the setup half-apply and leave a router with a veth that reaches nothing.
* **RouterOS REST wants the explicit verb.** ``POST /interface/bridge`` answers
  ``no such command``; the add is ``POST /interface/bridge/add`` with the
  arguments as a JSON body. Removal works either as
  ``POST /<menu>/remove`` with ``.id`` or as ``DELETE /<menu>/<id>`` depending on
  the release, so both are tried.
"""
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("mikroman.routeros")


class RouterOSCommandError(RuntimeError):
    """A RouterOS command was refused. Carries the router's own message."""

    def __init__(self, command: str, status_code: int, detail: str):
        super().__init__(f"{command} -> HTTP {status_code}: {detail}")
        self.command = command
        self.status_code = status_code
        self.detail = detail


def _as_list(raw: Any) -> List[Dict[str, Any]]:
    """RouterOS returns one object where a menu holds a single row."""
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    if isinstance(raw, dict):
        return [raw]
    return []


def _detail(resp) -> str:
    """The router's explanation, from whichever field it chose to use.

    ``detail`` comes first because that is where RouterOS puts the specific
    reason: an attempt to set an attribute this release does not have answers
    ``message: "Bad Request"`` with ``detail: "unknown parameter dns-servers"``.
    Reading the message alone threw away the one word that identified the fix.
    """
    try:
        body = resp.json()
    except Exception:
        return (resp.text or "")[:300]
    if isinstance(body, dict):
        for key in ("detail", "message", "error"):
            value = body.get(key)
            if value:
                return str(value)[:300]
    return str(body)[:300]


class ProvisioningMixin:
    """Create/inspect the bridges, veths, addresses and NAT rules a container needs."""

    # --- Reads used by the planner -------------------------------------------

    async def list_bridge_interfaces(self) -> List[Dict[str, Any]]:
        """Bridges on the router (``/interface/bridge``)."""
        async with self._get_client() as client:
            resp = await client.get("/interface/bridge")
            return _as_list(resp.json()) if resp.status_code == 200 else []

    async def list_veth_interfaces(self) -> List[Dict[str, Any]]:
        """vETH interfaces, one end of each container's virtual cable."""
        async with self._get_client() as client:
            resp = await client.get("/interface/veth")
            return _as_list(resp.json()) if resp.status_code == 200 else []

    async def list_bridge_ports(self) -> List[Dict[str, Any]]:
        """Members of every bridge (``/interface/bridge/port``)."""
        async with self._get_client() as client:
            resp = await client.get("/interface/bridge/port")
            return _as_list(resp.json()) if resp.status_code == 200 else []

    async def list_ip_addresses(self) -> List[Dict[str, Any]]:
        """Address/interface pairs, to catch a subnet the plan would collide with."""
        async with self._get_client() as client:
            resp = await client.get("/ip/address")
            return _as_list(resp.json()) if resp.status_code == 200 else []

    async def list_nat_rules(self, chain: Optional[str] = None) -> List[Dict[str, Any]]:
        """NAT rules, optionally narrowed to one chain."""
        async with self._get_client() as client:
            resp = await client.get("/ip/firewall/nat")
            if resp.status_code != 200:
                return []
            rules = _as_list(resp.json())
        if chain:
            rules = [r for r in rules if (r.get("chain") or "") == chain]
        return rules

    async def list_disks(self) -> List[Dict[str, Any]]:
        """Physical disks and partitions the router can see (``/disk``).

        RouterOS 7.13+ keeps storage state here rather than inferring it from
        ``/file``: one row per device and one per partition, with ``slot`` as the
        name everything else uses. This is the only trustworthy answer to "is the
        storage this container wants actually mounted, writable, and big enough" -
        ``/file`` says only that some path resolved.

        ``size``/``free`` are bytes; ``fs`` is ``-`` for a whole disk with no
        filesystem of its own; ``mount-point`` is the name the path starts with
        (``usb1-part1`` for ``/usb1-part1/...``).
        """
        props = ",".join((
            "slot", "name", "type", "fs", "mount-point", "mounted", "mount-read-only",
            "size", "free", "use", "formatting", "partition", "parent", "model",
            "serial", "temperature", "io-errors", "disabled", "block-device",
        ))
        async with self._get_client() as client:
            resp = await client.get("/disk", params={".proplist": props})
            return _as_list(resp.json()) if resp.status_code == 200 else []

    async def list_file_names(self) -> List[str]:
        """Names of the files and mount points on the router.

        ``.proplist`` is mandatory, not an optimisation: an unqualified
        ``GET /file`` returns every file's ``contents``, so one binary on flash -
        a ``.backup``, a video - makes the whole response undecodable as UTF-8.
        The same trap is recorded in ``docs/LESSONS.md`` against the backup
        listing, and this call is the only file listing provisioning needs anyway.
        """
        async with self._get_client() as client:
            resp = await client.get("/file", params={".proplist": "name"})
            if resp.status_code != 200:
                return []
            return [str(e.get("name")) for e in _as_list(resp.json()) if e.get("name")]

    # --- Writes ---------------------------------------------------------------

    async def _run(self, command: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """POST one explicit command and return the body, raising on refusal."""
        async with self._get_client() as client:
            resp = await client.post(command, json=payload)
            if resp.status_code not in (200, 201, 204):
                raise RouterOSCommandError(command, resp.status_code, _detail(resp))
            try:
                body = resp.json()
            except Exception:
                return {}
            return body if isinstance(body, dict) else {"result": body}

    async def add_bridge(self, name: str, comment: str) -> Dict[str, Any]:
        return await self._run("/interface/bridge/add", {"name": name, "comment": comment})

    async def add_veth(self, name: str, address: str, gateway: str) -> Dict[str, Any]:
        """Create the container end of the pair, addressed inside the container net."""
        return await self._run(
            "/interface/veth/add", {"name": name, "address": address, "gateway": gateway}
        )

    async def add_bridge_port(self, bridge: str, interface: str, comment: str) -> Dict[str, Any]:
        return await self._run(
            "/interface/bridge/port/add",
            {"bridge": bridge, "interface": interface, "comment": comment},
        )

    async def add_ip_address(self, address: str, interface: str, comment: str) -> Dict[str, Any]:
        return await self._run(
            "/ip/address/add", {"address": address, "interface": interface, "comment": comment}
        )

    async def add_nat_rule(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return await self._run("/ip/firewall/nat/add", args)

    async def format_disk(
        self,
        slot: str,
        file_system: str = "ext4",
        label: str = "",
        mbr_partition_table: bool = False,
    ) -> Dict[str, Any]:
        """Format a disk or partition. **This destroys everything on it.**

        The command is real and reachable over REST (``/disk format`` with
        ``file-system``, ``label`` and ``mbr-partition-table``; accepted
        filesystems are ``ext4``, ``fat32``, ``exfat``, ``xfs`` and ``btrfs``),
        which is exactly why nothing above this function may call it on a guess.
        Every guard - which slot may be touched, the confirmation the operator has
        to type, the refusal to wipe storage the running app is mounted on - lives
        in :mod:`backend.app.services.container_setup`, and the caller has to pass
        ``confirm`` equal to the slot name before this is reached at all.

        Formatting is asynchronous: the command returns while the device works,
        and the row reports ``formatting=true`` until it finishes.
        """
        payload: Dict[str, Any] = {"slot": slot, "file-system": file_system}
        if label:
            payload["label"] = label
        if mbr_partition_table:
            payload["mbr-partition-table"] = "yes"
        return await self._run("/disk/format", payload)

    async def remove_by_id(self, menu: str, item_id: str) -> bool:
        """Remove one row, trying both forms RouterOS has used across releases."""
        async with self._get_client() as client:
            resp = await client.post(f"{menu}/remove", json={".id": item_id})
            if resp.status_code in (200, 201, 204):
                return True
            resp = await client.delete(f"{menu}/{item_id}")
            return resp.status_code in (200, 201, 204)

    async def add_file(self, name: str, contents: str = "") -> bool:
        """Write a small text file through ``/file/add``.

        This is what creates a directory for a container mount, because RouterOS
        has no way to make one otherwise: there is no mkdir over REST, and on
        7.24.2 there is no upload endpoint at all - ``POST /upload`` answers
        "no such command or directory (upload)" for JSON and 415 for every body
        that is not JSON, so a multipart file transfer is not on the table.
        Writing ``<dir>/mikroman.keep`` lands the parent directory with it.

        Text only: ``contents`` goes through as a JSON string, which makes this
        useless for a database image. Big binaries have to leave over SFTP
        (``/user``'s own account, not the REST one) or Winbox.
        """
        async with self._get_client() as client:
            resp = await client.post("/file/add", json={"name": name, "contents": contents})
            if resp.status_code not in (200, 201, 204):
                raise RouterOSCommandError("/file/add", resp.status_code, _detail(resp))
            return True
