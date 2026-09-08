# ==============================================================================
# MikroTik RouterOS: manual fallback for running MikroMan in a container
# ==============================================================================
# Preferred path: the Containers page in MikroMan -> "Prepare this router for a
# container". It plans first (writes nothing), shows every step it will take,
# refuses to touch objects it did not create, and is safe to run twice. This file
# is for the case where there is no working MikroMan to do that - a bare router,
# or an install that cannot reach one.
#
# Run in a RouterOS terminal (/import setup_ros_container.rsc), section by section.
# Several steps below have a check to run first; do them.
# ==============================================================================

# ------------------------------------------------------------------------------
# 0. Container package.
#
# Optional package, present from RouterOS v7.13 on; enabling it needs a cold
# reboot. Check what this board actually reports instead of assuming:
#   /system/package print where name=container
# ------------------------------------------------------------------------------
# /system/device-mode/update container=yes

# ------------------------------------------------------------------------------
# 1. Storage. Do not skip this section.
#
# MikroMan's image layers unpack to roughly 340 MB, and several boards have only
# ~450-500 MB of internal flash free in total - enough for the pull to die
# halfway, and enough to wear out the flash that holds the router's own
# configuration. Layers and writable root both belong on external storage.
# ------------------------------------------------------------------------------
/container/config set layer-dir=usb1-part1/container-layers
/container/config set tmpdir=usb1-part1/container-tmp
# The container's resolver: the bridge gateway below runs RouterOS' own DNS
# resolver (check /ip/dns print -> allow-remote-requests).
/container/config set dns-servers=172.17.0.1

# The data directory must exist before it can be mounted, and RouterOS has no
# CLI mkdir for it: create it once in Winbox -> Files (right-click -> New Folder),
# or let MikroMan's setup apply create it by uploading into the path.
#   usb1-part1/mikroman_data/

# ------------------------------------------------------------------------------
# 2. Container network.
# ------------------------------------------------------------------------------
/interface/veth/add name=veth-mikroman address=172.17.0.2/24 gateway=172.17.0.1
/interface/bridge/add name=bridge-containers comment="mikroman:containers"
/interface/bridge/port/add bridge=bridge-containers interface=veth-mikroman comment="mikroman:container link"
/ip/address/add address=172.17.0.1/24 interface=bridge-containers comment="mikroman:container gateway"

# Before that last line, confirm the subnet is free on this router:
#   /ip/address print
# If another interface already carries something in 172.17.0.0/24, choose a
# different subnet. MikroMan's setup does this check and refuses rather than
# laying a second network over the first.

# ------------------------------------------------------------------------------
# 3. Outbound, so the image pull and the Telegram bot can reach the internet.
# ------------------------------------------------------------------------------
/ip/firewall/nat/add chain=srcnat src-address=172.17.0.0/24 action=masquerade \
    comment="mikroman:container outbound"

# ------------------------------------------------------------------------------
# 4. Publish the web UI - on the LAN only.
#
# `in-interface` is not optional. Without it the rule matches every interface the
# router has, ether1 (WAN) included, which puts an administrative UI - one that
# rewrites firewall rules and queue limits - in front of the internet.
# ------------------------------------------------------------------------------
/ip/firewall/nat/add chain=dstnat in-interface=br.lan dst-port=1928 protocol=tcp \
    action=dst-nat to-addresses=172.17.0.2 to-ports=1928 comment="mikroman:web ui, br.lan only"

# ------------------------------------------------------------------------------
# 5. Mount and container.
# ------------------------------------------------------------------------------
/container/mounts/add name=mikroman_data src=usb1-part1/mikroman_data dst=/data

# Credentials are NOT needed as environment when an existing installation is
# being carried over: router logins are stored in the database encrypted and so
# is the bot token, so copying `app.db` together with `.secret_key` into /data
# brings everything. The image already defaults DATABASE_URL to /data/app.db and
# PORT to 1928.
#
# Only a first-ever install with no database to import needs the block below -
# and treat anything written there as a shared secret afterwards, because it
# lands in plaintext in the running config and in every exported .rsc:
#   /container/envs/add name=mikroman_envs key=ROUTEROS_HOST value=172.17.0.1
#   /container/envs/add name=mikroman_envs key=ROUTEROS_PORT value=443
#   /container/envs/add name=mikroman_envs key=ROUTEROS_USER value=admin
#   /container/envs/add name=mikroman_envs key=ROUTEROS_PASSWORD value="..."
#   /container/envs/add name=mikroman_envs key=TELEGRAM_BOT_TOKEN value="..."
#   /container/envs/add name=mikroman_envs key=TELEGRAM_ADMIN_CHAT_IDS value="..."

# `envlist=` is dropped together with the block above; an unset list is normal.
/container/add remote-image=ghcr.io/masseselsev/mikroman:latest interface=veth-mikroman \
    mounts=mikroman_data start-on-boot=yes comment="mikroman:container"

# The published image is multi-arch (amd64, arm64, arm/v7); an arm64 board picks
# its own manifest, so no tag suffix is needed here.

# ------------------------------------------------------------------------------
# 6. Moving an existing installation onto the router, by hand, and why in this
#    order. This is the bare-router fallback: MikroMan has no feature for it, on
#    purpose — the copy is a one-time cutover, and left in the product it becomes
#    a button that replaces a live deployment's database.
#
#   1. apply the setup above - the container exists, nothing runs yet
#   2. carry the data in: app.db AND .secret_key -> the mount created above
#      (/usb1-part1/mikroman_data). Take the snapshot from the old side with
#      `sqlite3 old.db ".backup snapshot.db"` rather than copying the live file:
#      a database written every few seconds copied byte-for-byte arrives here as
#      "database disk image is malformed". Both files must land together — the
#      credentials and the bot token are stored encrypted, so app.db without the
#      matching .secret_key generates a new key on first boot and then cannot
#      decrypt any of them. Binary transfer is SFTP with one of the router's own
#      /user accounts; there is no upload endpoint over REST on 7.24.2.
#   3. start the container - the pull happens now, with the old instance still
#      the only writer
#   4. when its status reads `running`, stop the old instance
#
# 2 and 3 are not interchangeable: a container that boots against an empty /data
# writes a fresh database there, and the copy would then have to replace a file
# underneath a running application. Only one of the two may be writing.
# ------------------------------------------------------------------------------
