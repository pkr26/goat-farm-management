# Independent remote Firefox transport corroboration

[independent-read-only.json](independent-read-only.json) records the existing
official Playwright container's state, ARM64 runtime, exact loopback port
mapping, listening server log and successful host TCP/HTTP reachability at
19:45:20 UTC. No container/server was started, modified or restarted by this
reviewer, and no browser session or shared Playwright runner was launched.

The clinical reviewer separately proved client/server 1.62.1 connectivity,
Firefox 153.0 launch, a data URL and the unchanged localhost login page in
832 ms. The initial outer probe timeout concerned the Node process retaining
network-bridge handles after owned browser/context closure. Its final one-shot
probe explicitly exited zero in less than a second. Those browser observations
remain attributed to that reviewer; this receipt independently corroborates
the transport and container facts only.

Further independent probes stopped when the parent started the complete
Firefox suite. This transport receipt does not certify application journeys
or replace the final browser-suite evidence.
