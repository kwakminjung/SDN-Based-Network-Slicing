# CLAUDE.md — SDN Network Slicing Project

## Project Overview

**Course**: EC5209 Advanced Computer Networking, Spring 2026 — GIST EECS  
**Title**: SDN-Based Network Slicing with Mininet and Ryu: Implementation and Performance Evaluation  
**Student**: Minjung Kwak (20261053), Dept. of AI Convergence  
**Scope**: Solo project extending Lab 2 (SDN & Traffic Engineering)

The project emulates 5G-style network slicing on a shared physical topology, enforcing per-slice QoS using OpenFlow 1.3 flow rules and OVS queue configuration.

## Tech Stack

| Layer | Tool / Version |
|---|---|
| Network emulator | Mininet 2.3.x |
| SDN controller | Ryu (Python, OpenFlow 1.3) |
| Data plane / QoS | Open vSwitch (OVS) — HTB queuing |
| Measurement | iperf3 (throughput, jitter), ping (RTT) |
| Visualization | Python + matplotlib |
| Language | Python 3 |

## Project Structure (expected)

```
cn_proj/
├── topology/
│   └── topo.py          # Mininet topology (≥2 switches, 6 hosts)
├── controller/
│   └── slice_controller.py  # Ryu app — OpenFlow 1.3 flow rules + QoS
├── qos/
│   └── ovs_qos_setup.sh     # OVS queue configuration scripts
├── measurement/
│   ├── run_iperf.sh         # iperf3 experiment scripts
│   └── plot_results.py      # matplotlib graphs
├── results/                 # Raw iperf3 / ping output (CSV/JSON)
├── docs/
│   └── report.md            # Analysis and findings
└── CLAUDE.md
```

## Network Slice Design

| Slice | Priority | Policy | Traffic type |
|---|---|---|---|
| Slice A | High | 10 Mbps reserved, strict queue | URLLC-like (latency-sensitive control) |
| Slice B | Medium | 5 Mbps cap | eMBB-like (video/bulk transfer) |
| Slice C | Best Effort | Remaining bandwidth | Background / low-priority data |

Flow classification uses OpenFlow match fields: IP src/dst, TCP/UDP port.

## Key Objectives (deliverables)

1. **Baseline topology** — Mininet topo with ≥2 switches and 6 hosts; Ryu L2 forwarding baseline
2. **Slice enforcement** — OpenFlow rules + OVS HTB queues binding each host/flow to its slice
3. **Isolation verification** — Show Slice A guarantees hold under Slice B/C concurrent saturation load
4. **Performance measurement** — Throughput, latency, jitter tables and graphs with/without slicing
5. **Reproducible artifact** — All code + scripts runnable from scratch; final report + slides

## Development Guidelines

- Run Mininet as root (`sudo python3 topo.py`); Ryu controller runs separately as a non-root process
- Use `ovs-ofctl dump-flows` and `ovs-vsctl list qos` to verify queue state before running experiments
- Validate incrementally: two-slice setup first, then add Slice C
- Keep topology parametric (number of hosts/switches as CLI args) to allow easy re-runs
- Store all raw iperf3 output as JSON (`iperf3 -J`) for reproducible post-processing
- Do not hard-code IP addresses — derive from Mininet host objects

## OVS QoS Pattern

```bash
# Attach HTB queue to a port
ovs-vsctl set port <port> qos=@q \
  -- --id=@q create QoS type=linux-htb queues=0=@q0,1=@q1,2=@q2 \
  -- --id=@q0 create Queue other-config:min-rate=10000000 other-config:max-rate=10000000 \
  -- --id=@q1 create Queue other-config:max-rate=5000000 \
  -- --id=@q2 create Queue other-config:max-rate=100000000
```

OpenFlow action to enqueue: `actions=set_queue:<id>,output:<port>`

## Ryu Controller Conventions

- Inherit from `app_manager.RyuApp`; handle `EventOFPSwitchFeatures` to push initial flows
- Use `OFPFlowMod` with `OFPFC_ADD` and explicit `priority` to ensure slice rules beat the default L2 rule
- Install a table-miss flow (priority=0) for ARP/unknown traffic to fall through to L2 learning
- Log all flow installs at INFO level with slice label for debugging

## Measurement Protocol

1. Start Ryu controller, then Mininet
2. Run `pingall` to confirm L2 connectivity
3. Verify queue bindings (`ovs-vsctl list qos`)
4. **Baseline experiment**: all three slices idle, measure RTT
5. **Load experiment**: saturate Slice B and C with iperf3, measure Slice A throughput/RTT simultaneously
6. Repeat 3× per condition; report mean ± std
7. Save raw JSON, generate graphs with `plot_results.py`

## Anticipated Challenges & Mitigations

- **OVS queue binding errors**: verify with `ovs-ofctl dump-flows` and `ovs-vsctl` after each step; fix two-slice case before extending to three
- **Scope creep**: dynamic reconfiguration and web dashboard are **stretch goals only** — core scope is frozen at 3 slices on 1 static topology

## Timeline

| Date | Milestone |
|---|---|
| April 18, 2026 | Proposal submitted |
| May 16, 2026 | Progress report |
| June 6, 2026 | Final report + code repo + slides |
| June 13, 2026 | Live demo + final presentation |
