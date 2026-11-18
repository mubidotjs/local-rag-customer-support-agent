# Offline ingress diagnostic preparation

- Historical baseline: `6923dbdeb1e7d89637ea1daac188f2f2e451895c`.
- Diagnostic: `container-port-diag-5b-once`.
- Frozen plan SHA-256: `fd44d2a2f8a2400d8388f1df0cdd1a460ab47d86b3b1a08b4b958a9a393bae74`.
- Historical plan SHA-256: `3be4f849bff92b4e836328f4082b8a531259349814a3b145fac870602296c0e3`.
- Historical manifest SHA-256: `77fd1dccf602fc6669ef0d63965b03f7754357fac5e435d60041b48989d7e6df`.
- Consumed archive checksum manifest SHA-256: `217da0328f7361b5e81e7cdfc6d13efa0bc59d0d34b9d02e6c34652474b1d050`.

Verified 397/397 historical artifact hashes (33 ignored files read from the
original checkout) and 31/31 consumed archive hashes. Original paths, contents,
and claims were left in place. The archived inspection receipt itself is the
negative mapping test input.

The deliverable source SHA is the commit introducing this report. The pinned
plan, two fake-transport branches, failure cleanup, and budget checks run in
ordinary CI without the live entry point. The runtime-preparation workflow
publishes branch coverage for the fake checks. No responder, Docker acceptance,
diagnostic HTTP request, or Ollama request was executed. No live claim was
created. Listener and port checks are specified in the [design and authorization
proposal](../../docs/container-port-diagnostic.md); the transport remains
unverified on the target runtime until separately authorized.
