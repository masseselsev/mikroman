# ==============================================================================
# MikroTik RouterOS 7.24+ Container Setup Script for MikroMan
# ==============================================================================
# Run this script directly in RouterOS Terminal (/import setup_ros_container.rsc)
# or paste commands section by section.
# ==============================================================================

# 1. Enable Container Feature (Requires cold reboot on initial activation)
# /system/device-mode/update container=yes

# 2. Setup VETH Network Interface for Container
/interface/veth/add name=veth-mikroman address=172.17.0.2/24 gateway=172.17.0.1

# 3. Create Docker Bridge & Attach VETH
/interface/bridge/add name=bridge-containers comment="MikroMan Containers Bridge"
/interface/bridge/port/add bridge=bridge-containers interface=veth-mikroman
/ip/address/add address=172.17.0.1/24 interface=bridge-containers comment="Container Gateway IP"

# 4. Configure NAT Masquerade for Outbound Container Traffic (for Telegram Bot & DNS)
/ip/firewall/nat/add chain=srcnat src-address=172.17.0.0/24 action=masquerade comment="MikroMan Container Internet NAT"

# 5. Optional: Port Forward Container Web UI to LAN (Port 1928 -> 172.17.0.2:1928)
/ip/firewall/nat/add chain=dstnat dst-port=1928 protocol=tcp action=dst-nat to-addresses=172.17.0.2 to-ports=1928 comment="MikroMan Web UI Access"

# 6. Configure Environment Variables
/container/envs/add name=mikroman_envs key=ROUTEROS_HOST value=172.17.0.1
/container/envs/add name=mikroman_envs key=ROUTEROS_PORT value=443
/container/envs/add name=mikroman_envs key=ROUTEROS_USER value=admin
/container/envs/add name=mikroman_envs key=ROUTEROS_PASSWORD value="YOUR_ROUTER_PASSWORD"
/container/envs/add name=mikroman_envs key=TELEGRAM_BOT_TOKEN value="YOUR_TELEGRAM_TOKEN"
/container/envs/add name=mikroman_envs key=TELEGRAM_ADMIN_CHAT_IDS value="YOUR_CHAT_ID"

# 7. Configure Container Storage Mounts (Point to USB drive or disk1 to preserve internal flash)
/container/mounts/add name=mikroman_data src=/usb1-part1/mikroman_data dst=/data

# 8. Create & Start MikroMan Container
/container/add remote-image=ghcr.io/mikroman/mikroman:latest interface=veth-mikroman envlist=mikroman_envs mounts=mikroman_data start-on-boot=yes comment="MikroMan Companion"
