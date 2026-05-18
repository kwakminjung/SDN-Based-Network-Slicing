# CLAUDE.md — SDN Network Slicing Project

## Project Overview

**Course**: EC5209 Advanced Computer Networking, Spring 2026 — GIST EECS  
**Title**: SDN-Based Network Slicing with Mininet and os-ken: Implementation and Performance Evaluation  
**Student**: Minjung Kwak (20261053), Dept. of AI Convergence  
**Scope**: Solo project extending Lab 2 (SDN & Traffic Engineering)

The project emulates 5G-style network slicing on a shared physical topology, enforcing per-slice QoS using OpenFlow 1.3 flow rules and OVS queue configuration. Based on advisor feedback, the scope is extended to include multi-variable QoS control and (stretch goal) an autonomous slicing agent powered by Gemma4.

## Tech Stack

| Layer | Tool / Version |
|---|---|
| Network emulator | Mininet 2.3.0 |
| SDN controller | os-ken 2.0.0 (Ryu-compatible fork, OpenFlow 1.3) |
| Python runtime | Python 3.10.14 via pyenv (virtualenv: `sdn-env`) |
| Data plane / QoS | Open vSwitch 3.3.4 — HTB queuing + tc netem |
| Measurement | iperf3 (throughput, jitter), ping (RTT) |
| Visualization | Python + matplotlib |
| Agent (stretch) | Gemma4 via Ollama (local) or Google AI Studio API |

> **Note on controller selection**: Ryu was originally planned but is incompatible with Ubuntu 24.04/26.04 (Python 3.10+ incompatibility with eventlet/greenlet/dnspython dependency chain). os-ken was selected as a drop-in replacement — a Ryu fork maintained by the OpenStack community with identical OpenFlow API and full Python 3.10 support. os-ken 2.0.0 does not include built-in example apps, so all controller logic is written from scratch.

## Environment Setup

```bash
# Activate virtual environment (required before any controller or topology work)
pyenv activate sdn-env

# Run controller (Terminal 1)
python -m os_ken.cmd.manager <app.py>

# Run topology (Terminal 2, as root)
sudo python3 topology.py
```

**Version check commands:**
```bash
python -m os_ken.cmd.manager --version   # osken-manager 2.0.0
mn --version                             # 2.3.0
ovs-vsctl --version                      # 3.3.4 (host PC) / 3.7.1 (VM)
```

## Project Structure

```
SDN-Based-Network-Slicing/
├── topology.py              # Mininet topology (2 switches, 6 hosts, TCLink)
├── l2_switch.py             # Baseline L2 forwarding controller (os-ken)
├── controller/
│   └── slice_controller.py  # (TODO) os-ken app — OpenFlow 1.3 + multi-variable QoS
├── qos/
│   └── ovs_qos_setup.sh     # (TODO) OVS HTB queue + tc netem configuration
├── measurement/
│   ├── run_iperf.sh         # (TODO) iperf3 experiment scripts
│   └── plot_results.py      # (TODO) matplotlib graphs
├── agent/
│   └── slicing_agent.py     # (STRETCH) Gemma4-based autonomous slicing agent
├── results/                 # Raw iperf3 / ping output (CSV/JSON)
├── docs/
│   └── report.md            # Analysis and findings
└── CLAUDE.md
```

## Network Slice Design

| Slice | Priority | Bandwidth | Latency | Loss | Hosts | IP Addresses |
|---|---|---|---|---|---|---|
| Slice A | High | 10 Mbps reserved | < 10ms | 0% | H1 ↔ H4 | 10.0.0.1 ↔ 10.0.0.4 |
| Slice B | Medium | 5 Mbps cap | < 50ms | < 1% | H2 ↔ H5 | 10.0.0.2 ↔ 10.0.0.5 |
| Slice C | Best Effort | Remaining BW | no guarantee | no guarantee | H3 ↔ H6 | 10.0.0.3 ↔ 10.0.0.6 |

## Topology Design

- 2 OVS switches (S1, S2), 6 hosts (H1–H6)
- S1: H1 (Slice A), H2 (Slice B), H3 (Slice C)
- S2: H4 (Slice A), H5 (Slice B), H6 (Slice C)
- S1–S2 link: **100 Mbps** (deliberate bottleneck — QoS measurement point)
- Host–switch links: 1 Gbps
- OpenFlow version: 1.3

## Variable Expansion Plan (advisor feedback)

### Phase 1 — Currently implemented
- Bandwidth (Mbps) per slice via OVS HTB queues

### Phase 2 — To be implemented

**Traffic classification variables**
- TCP/UDP port number (e.g. port 5001 → Slice A, port 5002 → Slice B, port 5003 → Slice C)
- Protocol type (TCP vs UDP)
- DSCP marking (priority bits in IP header)

**QoS variables**
- Latency limit per slice — `tc netem delay` on OVS ports
- Jitter limit per slice — `tc netem delay Xms Yms` (variation parameter)
- Packet loss rate — `tc netem loss X%` per slice
- HTB burst size parameter

**Topology variables**
- Number of switches/hosts as CLI arguments (parametric topology)
- Multi-hop path (extend S1–S2 to S1–S2–S3)

### Phase 3 — Stretch goal: Gemma4 Autonomous Slicing Agent

```
iperf3/ping measurements (per slice)
        ↓
Metrics collector (Python, periodic)
        ↓
Gemma4 agent
"Slice A throughput dropped below guarantee → adjust queue"
        ↓
os-ken REST API
        ↓
OVS queue reconfiguration (dynamic slicing)
```

- Gemma4 runs locally via Ollama or via Google AI Studio API
- Agent receives current slice metrics as structured text prompt
- Agent outputs adjustment commands (which queue, new rate)
- os-ken REST API applies the changes without restarting the controller

## Key Objectives (deliverables)

1. **Baseline topology** ✅ — Mininet topo with 2 switches and 6 hosts; os-ken L2 forwarding baseline
2. **Slice enforcement** — OpenFlow rules + OVS HTB queues + tc netem binding each flow to its slice
3. **Isolation verification** — Show Slice A guarantees hold under Slice B/C concurrent saturation load
4. **Multi-variable measurement** — Throughput, latency, jitter, loss tables and graphs with/without slicing
5. **Reproducible artifact** — All code + scripts runnable from scratch; final report + slides
6. **Autonomous agent** *(stretch)* — Gemma4-based dynamic slice reconfiguration

## Current Progress (as of May 16, 2026)

| Milestone | Status | Notes |
|---|---|---|
| Environment setup (host PC) | ✅ Done | os-ken 2.0.0, Mininet 2.3.0, OVS 3.3.4 |
| Baseline topology | ✅ Done | topology.py — pingall 0% dropped (30/30) |
| L2 forwarding controller | ✅ Done | l2_switch.py — MAC learning, flow install |
| Code migrated to GitHub | ✅ Done | Running on host PC (RISENUC15-01) |
| Variable expansion (port/DSCP/latency/jitter/loss) | 🔲 Next | Phase 2 |
| OVS QoS queue setup | 🔲 Next | HTB 3-queue + tc netem on S1–S2 link |
| Slicing controller | 🔲 Next | Multi-variable flow classification |
| Performance measurement | 🔲 Pending | iperf3 throughput/jitter, ping RTT |
| Result visualization | 🔲 Pending | matplotlib graphs |
| Gemma4 slicing agent | 🔲 Stretch | After core slicing is verified |
| Final report + slides | 🔲 Pending | Due June 6 |

## Development Guidelines

- Always activate `sdn-env` before running any Python code
- Run Mininet as root (`sudo python3 topology.py`); os-ken controller runs separately as non-root
- Use `ovs-ofctl dump-flows` and `ovs-vsctl list qos` to verify queue/flow state before experiments
- Validate incrementally: two-slice setup first, then add Slice C
- Store all raw iperf3 output as JSON (`iperf3 -J`) for reproducible post-processing
- Do not hard-code IP addresses — derive from Mininet host objects

## OVS QoS Pattern

```bash
# Attach HTB queue to the S1-S2 bottleneck port
ovs-vsctl set port <port> qos=@q \
  -- --id=@q create QoS type=linux-htb queues=0=@q0,1=@q1,2=@q2 \
  -- --id=@q0 create Queue other-config:min-rate=10000000 other-config:max-rate=10000000 \
  -- --id=@q1 create Queue other-config:max-rate=5000000 \
  -- --id=@q2 create Queue other-config:max-rate=100000000

# Add latency/jitter/loss on top with tc netem
sudo tc qdisc add dev <iface> parent 1:1 handle 10: netem delay 10ms
sudo tc qdisc add dev <iface> parent 1:2 handle 20: netem delay 50ms 10ms loss 1%
```

OpenFlow action to enqueue: `actions=set_queue:<id>,output:<port>`

## os-ken Controller Conventions

- Inherit from `app_manager.OSKenApp` (not `RyuApp`)
- Handle `EventOFPSwitchFeatures` to push initial flows on switch connect
- Use `OFPFlowMod` with `OFPFC_ADD` and explicit `priority` to ensure slice rules beat the default L2 rule
- Install a table-miss flow (priority=0) for ARP/unknown traffic to fall through to L2 learning
- Match on IP src/dst AND TCP/UDP port for multi-variable classification
- Log all flow installs at INFO level with slice label for debugging
- Import from `os_ken.*` (not `ryu.*`)

## Measurement Protocol

1. Start os-ken controller, then Mininet
2. Run `pingall` to confirm L2 connectivity
3. Verify queue bindings (`ovs-vsctl list qos`)
4. **Baseline experiment**: all three slices idle, measure RTT and throughput
5. **Load experiment**: saturate Slice B and C with iperf3, measure Slice A throughput/RTT/jitter simultaneously
6. Repeat 3× per condition; report mean ± std
7. Save raw JSON (`iperf3 -J`), generate graphs with `plot_results.py`

## Anticipated Challenges & Mitigations

- **OVS queue binding errors**: verify with `ovs-ofctl dump-flows` and `ovs-vsctl` after each step; fix two-slice case before extending to three
- **tc netem + HTB interaction**: apply netem as a child qdisc under HTB, not as a replacement
- **Gemma4 response latency**: agent loop period should be ≥5s to avoid thrashing; add hysteresis to prevent oscillation
- **Scope creep**: Gemma4 agent is stretch goal only — core scope frozen at 3 slices, static topology, multi-variable QoS

## Timeline

| Date | Milestone | Status |
|---|---|---|
| April 18, 2026 | Proposal submitted | ✅ Done |
| May 16, 2026 | Progress report | ✅ Done |
| May 말 | Variable expansion + slicing controller | 🔲 In progress |
| June 초 | Measurement + Gemma4 agent prototype | 🔲 Pending |
| June 6, 2026 | Final report + code repo + slides | 🔲 Pending |
| June 13, 2026 | Live demo + final presentation | 🔲 Pending |