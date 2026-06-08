# SDN-Based Network Slicing — Smart City SFC

Language: [한국어](README.md) | **English**

**EC5209 Advanced Computer Networking, Spring 2026 — GIST EECS**  
Minjung Kwak (20261053), Dept. of AI Convergence

This project implements 5G-style network slicing for a smart city scenario using Mininet, os-ken (OpenFlow 1.3), and OVS HTB queuing.  
The key idea is that **each slice physically traverses a different NFV chain (Service Function Chaining)**. Latency differentiation arises naturally from hop count differences, not from injected netem delays.  
Clients do not specify a slice. The Gemma4 agent automatically assigns one based on hostname, traffic pattern, and client requirements.

---

## Topology

```
Clients (S1)           Edge Cloud (S_edge)              Core Cloud (S_core)
vehicle_01 (10.0.0.1) ─┐  ┌─ nfv_fw   (10.1.0.1)        ┌─ AutoDrive Hub  (10.0.0.4)
camera_01  (10.0.0.2) ─┤──┤  nfv_cache (10.1.0.2)   ──   ├─ EntertainPort  (10.0.0.5)
sensor_01  (10.0.0.3) ─┘  └─ nfv_aggr  (10.1.0.3)        └─ CityPulse Hub  (10.0.0.6)
+ dynamic clients (10.0.0.7~)
```

- **S1–S_edge, S_edge–S_core**: 100 Mbps
- **Host–switch links**: 1 Gbps
- **OpenFlow**: 1.3

---

## Per-Slice SFC Chains

Each slice traverses a distinct NFV chain. Traffic **physically passes through** each intermediate node.

```
URLLC: S1 → S_edge → [nfv_fw]                    → S_core → AutoDrive Hub   (1 hop)
eMBB:  S1 → S_edge → [nfv_fw] → [nfv_cache]      → S_core → EntertainPort  (2 hops)
mMTC:  S1 → S_edge → [nfv_fw] → [nfv_aggr]       → S_core → CityPulse Hub  (2 hops)
```

| NFV | Function | Traversed by |
|-----|----------|-------------|
| `nfv_fw` | Firewall (transit + log) | URLLC / eMBB / mMTC (all slices) |
| `nfv_cache` | Content cache (transit + log) | eMBB only |
| `nfv_aggr` | Data aggregation (transit + log) | mMTC only |

> NFV nodes do not implement real functionality. They receive, log, and re-forward packets. What matters is that traffic physically traverses each node.

---

## SLA Requirements (3GPP TS 23.501)

| Slice | Server | GBR | MBR | PDB | PER | Use Case |
|-------|--------|-----|-----|-----|-----|---------|
| **URLLC** | AutoDrive Hub | 10 Mbps | 10 Mbps | 1 ms | 10⁻⁵ | Autonomous driving, V2X |
| **eMBB** | EntertainPort | 20 Mbps | 50 Mbps | 100 ms | 10⁻⁶ | HD streaming, CCTV |
| **mMTC** | CityPulse Hub | 1 Mbps | 10 Mbps | 300 ms | 10⁻² | IoT sensors, smart meters |

HTB queues are used solely for GBR/MBR enforcement. netem is not used.

---

## Automatic Hostname Classification

Clients do not select a slice manually. The slice is assigned automatically based on the hostname prefix.

| Hostname prefix | Slice | Server | Gemma4 invoked |
|----------------|-------|--------|---------------|
| `vehicle_*`, `car_*`, `v2x_*`, `ambulance_*` | URLLC | AutoDrive Hub | ❌ Rule-based, immediate |
| `camera_*`, `cctv_*`, `stream_*`, `cam_*` | eMBB | EntertainPort | ❌ Rule-based, immediate |
| `sensor_*`, `iot_*`, `meter_*`, `light_*` | mMTC | CityPulse Hub | ❌ Rule-based, immediate |
| (others: `device_*`, `unknown_*`, etc.) | mMTC (default) | CityPulse Hub | ✅ Gemma4 decides |

---

## Tech Stack

| Layer | Tool |
|-------|------|
| Network emulator | Mininet 2.3.0 |
| SDN controller | os-ken 2.0.0 (OpenFlow 1.3) |
| Python | 3.10.14 (pyenv virtualenv: `sdn-env`) |
| Data plane / QoS | Open vSwitch 3.3.4 — HTB queuing (no netem) |
| NFV | Python + scapy (promiscuous receive + forward) |
| AI agent | Gemma4 via Ollama `/api/chat` (system/user role separation) |
| Measurement | iperf v2 (UDP throughput), ping (RTT) |
| Dashboard | Python rich (real-time SFC path display) |

> **Why os-ken instead of Ryu**: Ryu is incompatible with Ubuntu 24.04 / Python 3.10+ due to eventlet/greenlet dependency conflicts. os-ken is a community-maintained Ryu fork with an identical OpenFlow API and full Python 3.10 support.

---

## Project Structure

```
SDN-Based-Network-Slicing/
├── config.py                    # SFC chain definitions, slice policies, hostname rules
├── topology.py                  # 3-switch topology (S1 + S_edge + S_core + NFV hosts)
├── nfv/
│   ├── nfv_base.py              # Shared NFV logic (scapy promiscuous + log + forward)
│   ├── nfv_fw.py                # Firewall NFV
│   ├── nfv_cache.py             # Cache NFV (eMBB only)
│   └── nfv_aggr.py              # Aggregation NFV (mMTC only)
├── controller/
│   ├── l2_switch.py             # Baseline L2 forwarding controller
│   └── slice_controller.py      # SFC controller (3 switches + REST API)
├── agent/
│   └── slicing_agent.py         # Gemma4 agent (classification + SLA monitoring)
├── demo/
│   ├── dashboard.py             # Real-time TUI (SFC path + GBR status)
│   └── request_injector.py      # Interactive service request terminal
└── measurement/
    └── run_measurement.py       # Measurement script (GBR/MBR/PDB verification)
```

---

## System Architecture

<img width="2352" height="1312" alt="image" src="https://github.com/user-attachments/assets/770b5657-f58a-4f3f-a0e0-39f97f4f9f33" />


```
Client (vehicle_01)
        │ First packet → Packet-In
        ▼
┌──────────────────────────────────────────────────────┐
│              slice_controller (os-ken)               │
│                                                      │
│  S1: hostname lookup → classify_hostname()           │
│    Clear prefix  → install HTB queue + flow rule     │
│    Ambiguous     → async Gemma4 classification       │
│      inputs: hostname + protocol + dst_port          │
│              + pkt_size + requirements               │
│                                                      │
│  S_edge: SFC rules (in_port + dst_ip → NFV routing)  │
│    URLLC: in_port=S1 → nfv_fw → s_core              │
│    eMBB:  in_port=S1 → nfv_fw → nfv_cache → s_core  │
│    mMTC:  in_port=S1 → nfv_fw → nfv_aggr  → s_core  │
│                                                      │
│  S_core: dst_ip → server port forwarding             │
└──────────────────────────────────────────────────────┘
        │ OpenFlow FlowMod
        ▼
┌─────────────────────────────────────────────────────┐
│  OVS Switches                                        │
│  S1:     HTB queue (GBR/MBR enforcement)             │
│  S_edge: SFC routing (latency from hop count)        │
│  S_core: server forwarding                           │
└──────┬──────────────────────────────────────────────┘
       │
       ├─ [nfv_fw]    ← all slices transit (scapy recv + log + forward)
       ├─ [nfv_cache] ← eMBB only
       └─ [nfv_aggr]  ← mMTC only
```

---

## SFC Implementation

### S_edge Flow Rules

S_edge routes packets through the SFC chain using `in_port + dst_ip` combinations.

```
# URLLC (dst=10.0.0.4)
in_port=S1_port,      dst=10.0.0.4 → output(nfv_fw_port)
in_port=nfv_fw_port,  dst=10.0.0.4 → output(s_core_port)

# eMBB (dst=10.0.0.5)
in_port=S1_port,        dst=10.0.0.5 → output(nfv_fw_port)
in_port=nfv_fw_port,    dst=10.0.0.5 → output(nfv_cache_port)
in_port=nfv_cache_port, dst=10.0.0.5 → output(s_core_port)

# mMTC (dst=10.0.0.6)
in_port=S1_port,       dst=10.0.0.6 → output(nfv_fw_port)
in_port=nfv_fw_port,   dst=10.0.0.6 → output(nfv_aggr_port)
in_port=nfv_aggr_port, dst=10.0.0.6 → output(s_core_port)
```

Each NFV script receives a packet, logs it, and **re-sends it on the same interface**. S_edge identifies `in_port=nfv_port` and routes to the next hop.

### HTB Queue Configuration (`s1-eth4`, S1 → S_edge)

```
HTB root (100 Mbps)
├── Queue 0 / class 1:1 → URLLC (GBR=MBR=10 Mbps)
├── Queue 1 / class 1:2 → eMBB  (GBR=20 Mbps, MBR=50 Mbps)
└── Queue 2 / class 1:3 → mMTC  (GBR=1 Mbps,  MBR=10 Mbps)
```

No netem. Latency differences emerge naturally from the SFC hop count.

### Flow Priority Table

| Priority | Switch | Match | Action | Purpose |
|----------|--------|-------|--------|---------|
| 10 | S1 | `in_port + IP + src + dst` | `set_queue(n) + output(S_edge)` | HTB queue assignment |
| 10 | S_edge | `in_port + IP + dst` | `output(next_hop)` | SFC transit routing |
| 10 | S_core | `IP + dst` | `output(server_port)` | Server forwarding |
| 1 | all | `in_port + eth_dst` | `output(port)` | L2 forwarding |
| 0 | all | (any) | `→ controller` | table-miss |

---

## Gemma4 Agent

### When Gemma4 Is Invoked

| Situation | Handling |
|-----------|---------|
| Clear hostname prefix (`vehicle_*`, etc.) | Rule-based, immediate — Gemma4 **not called** |
| Ambiguous hostname (`device_01`, etc.) | Gemma4 async classification → re-installs flow rule if result differs |
| GBR violation detected | Gemma4 queried for reassignment decision |
| Explicit `/slices/request` call | Gemma4 selects optimal slice based on current load |

### Inputs for Ambiguous Hostname Classification

For clients with no matching prefix, the following information is automatically extracted from the first Packet-In and combined with user-declared requirements.

| Input | Source | Example |
|-------|--------|---------|
| hostname | Client registration | `device_01` |
| protocol | Packet-In (IP header) | `UDP` |
| dst_port | Packet-In (TCP/UDP header) | `1234` |
| pkt_size | Packet-In (`len(msg.data)`) | `64 bytes` |
| requirements | `add_client(requirements=...)` | `latency < 5ms, bandwidth 8Mbps` |

### System / User Message Separation

Uses Ollama `/api/chat` endpoint to separate roles.

```python
messages = [
    {
        "role": "system",
        # Fixed instructions — role definition, slice descriptions, JSON output format
        "content": "You are an SDN smart city network manager ..."
    },
    {
        "role": "user",
        # Per-request data — hostname, traffic pattern, current load
        "content": "Hostname: device_01\nProtocol: UDP\n..."
    }
]
```

All prompts are written in English for better accuracy and response speed with Gemma4.

### Gemma4 Response Time

Measured inside `ask_gemma()` and logged for every call:
```
Gemma4 latency: 2.34 s
```

### Example (Controller Log)

```
# device_01 connects — no matching prefix → assigned mmtc by default, Gemma4 queried async
Auto-classified: device_01 → MMTC (default, Gemma4 pending)

# Gemma4 responds → overrides to urllc, flow rule re-installed
Gemma4: device_01 → urllc
        (device_01 handles autonomous driving and V2X traffic, URLLC is appropriate)
Gemma4 override: device_01 → urllc (was mmtc)
```

### REST API (port 8080)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/slices` | Slice state + SFC chains + active connections |
| POST | `/slices/reassign` | Direct reassignment (applied immediately) |
| POST | `/slices/request` | Gemma4 load-aware optimal slice assignment |
| POST | `/clients/register` | Register client hostname + requirements |

---

## Running the Project

### Prerequisites

```bash
pyenv activate sdn-env

# scapy — required by NFV scripts (must be installed as a system package)
sudo apt install python3-scapy

# Ollama + Gemma4 (required for the agent)
ollama serve &
ollama pull gemma4
```

> **Note**: scapy runs inside Mininet network namespaces using the system Python interpreter. Use `sudo apt install python3-scapy` instead of `pip install scapy`.

### 1. Controller (Terminal 1, non-root)

```bash
python -m os_ken.cmd.manager controller.slice_controller
```

Expected startup log:
```
S1 rule: vehicle_01 (10.0.0.1) queue=0 → [nfv_fw] → AutoDrive Hub
S1 rule: camera_01  (10.0.0.2) queue=1 → [nfv_fw → nfv_cache] → EntertainPort
S1 rule: sensor_01  (10.0.0.3) queue=2 → [nfv_fw → nfv_aggr] → CityPulse Hub
REST API listening on port 8080
```

### 2. Topology (Terminal 2, root required)

```bash
sudo mn -c                          # Clean up any leftover state from previous runs
sudo python3 topology.py            # Mininet CLI mode
sudo python3 topology.py --measure  # Automated measurement then exit
```

NFV scripts start automatically when the topology is launched.

### 3. NFV Transit Logs

```bash
tail -f /tmp/nfv_fw.log    # Firewall transit — logs all slices
tail -f /tmp/nfv_cache.log # Cache transit — logs eMBB traffic only
tail -f /tmp/nfv_aggr.log  # Aggregation transit — logs mMTC traffic only
```

### 4. Agent (Terminal 3)

```bash
python agent/slicing_agent.py           # GBR monitoring loop (10 s interval)
python agent/slicing_agent.py --dry-run # Analysis only, no reassignment
```

### 5. Demo Dashboard (Terminal 4)

```bash
python demo/dashboard.py
```

Example dashboard output:
```
🚗 vehicle_01  URLLC  [nfv_fw]                  → AutoDrive Hub
📺 camera_01   eMBB   [nfv_fw] → [nfv_cache]    → EntertainPort
🏙️ sensor_01   mMTC   [nfv_fw] → [nfv_aggr]     → CityPulse Hub
```

### 6. Bandwidth Measurement (Mininet CLI)

> **Note**: iperf3 fails in Mininet namespaces due to a cookie handshake timeout. Use **iperf v2** instead.  
> Avoid port 5001, which may conflict with a host-level iperf3 server. Use ports **6001–6003**.

```
# Resolve ARP and pre-install flow rules with a ping first
mininet> vehicle_01 ping -c 2 10.0.0.4

# iperf v2 UDP — URLLC
mininet> autodrive iperf -s -u -p 6001 &
mininet> vehicle_01 iperf -c 10.0.0.4 -u -p 6001 -b 15M -t 5

# iperf v2 UDP — eMBB
mininet> ent_port iperf -s -u -p 6002 &
mininet> camera_01 iperf -c 10.0.0.5 -u -p 6002 -b 50M -t 5

# iperf v2 UDP — mMTC
mininet> citypulse iperf -s -u -p 6003 &
mininet> sensor_01 iperf -c 10.0.0.6 -u -p 6003 -b 10M -t 5
```

### 7. Dynamic Client Addition (Mininet CLI)

```python
# add_client / net / s1 are injected into builtins — usable directly from py

# Slice assigned automatically from hostname prefix (no Gemma4 call)
py add_client(net, 'vehicle_02', s1)   # → 10.0.0.7, assigned URLLC
py add_client(net, 'camera_02',  s1)   # → 10.0.0.8, assigned eMBB

# Ambiguous hostname + requirements → Gemma4 decides slice
py add_client(net, 'device_01', s1, requirements='latency < 5ms, bandwidth 8Mbps')
```

### 8. Verification

```bash
sudo ovs-ofctl -O OpenFlow13 dump-flows s1      # S1 HTB queue assignment rules
sudo ovs-ofctl -O OpenFlow13 dump-flows sedge   # S_edge SFC routing rules
sudo ovs-ofctl -O OpenFlow13 dump-flows s_core  # S_core server forwarding rules
tc -s class show dev s1-eth4                    # Live HTB queue statistics
curl localhost:8080/slices                      # Slice state + SFC chains + connections
```

---

## Measurement Results

iperf v2 UDP, 2026-06-05 (RISENUC15-01):

| Slice | GBR | Measured (Mbps) | SLA |
|-------|-----|----------------|-----|
| URLLC | 10 Mbps | **10.00** | ✅ GBR OK |
| eMBB | 20 Mbps | **32.47** | ✅ GBR OK |
| mMTC | 1 Mbps | **5.42** | ✅ GBR OK |

- URLLC is capped at GBR=MBR=10 Mbps, confirming exact rate enforcement.
- eMBB and mMTC both exceed GBR while staying below MBR, confirming correct HTB isolation.

---

## Known Issues and Fixes

### iperf3 Incompatibility with Mininet Namespaces

iperf3 fails at the cookie handshake step in Mininet.  
**Fix**: Use **iperf v2**, which has no cookie handshake and works reliably in Mininet.

### scapy Installation

NFV scripts run under the system Python interpreter inside Mininet namespaces.  
**Fix**: `sudo apt install python3-scapy` to install as a system package.

### NFV Re-receive Loop Prevention

When scapy re-sends a packet on the same interface, it would normally be sniffed again, creating a loop.  
**Fix**: `filter="ip and not ether src {own_mac}"` — packets whose Ethernet source is the NFV's own MAC are ignored.

### ovs-ofctl OpenFlow Version Mismatch

`ovs-ofctl dump-flows` defaults to OpenFlow 1.0, causing version negotiation failure.  
**Fix**: `sudo ovs-ofctl -O OpenFlow13 dump-flows <switch>`

### Mininet Leftover State (`RTNETLINK: File exists`)

If a previous run exits abnormally, veth interfaces remain and block the next launch.  
**Fix**: Run `sudo mn -c` to clean up before restarting.

### `os_ken.app.wsgi` Module Not Found

os-ken 2.0.0 does not include Ryu's `wsgi` module.  
**Fix**: Replaced with `eventlet.wsgi` + `hub.spawn()`.

### OVS `other-config:default-queue` Ignored

OVS always sets the HTB default to Queue 0 regardless of the `default-queue` setting. All traffic is classified by explicit OpenFlow rules, so this has no effect on measurements.

---

## Branch Structure

| Branch | Contents |
|--------|---------|
| `main` | Baseline implementation (S1–S2, HTB + netem, static slices) |
| `feature/sfc` | **SFC-based redesign** — physical NFV transit + Gemma4 auto-classification + verified |
