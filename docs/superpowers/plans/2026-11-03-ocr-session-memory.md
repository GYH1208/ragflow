# OCR session memory repair

**Goal:** Make the existing OCR CPU memory and thread settings take effect without breaking inference.

**Approved scope:** The user requested direct repair after the isolated reproduction. Keep the existing model cache and GPU options. Apply the tested repair to the live checkout and restart only the idle task executor.

**Implementation:** Change both ONNX constructors to `sess_options=options`. Remove CPU arena shrinkage because the CPU arena is disabled. Preserve optional GPU arena shrinkage.

**Validation:**

- [x] Add regression tests using real ONNX Runtime and its bundled tiny model: effective CPU settings, working CPU inference, cached session reuse, and CUDA configuration forwarding without GPU hardware.
- [x] Confirm tests fail on the original loader, then pass after the minimal change.
- [x] Run the relevant parser tests and check the diff; record environmental test failures separately.
- [x] Apply only reviewed repair files to the live checkout, preserving existing unrelated edits.
- [x] Capture pre-restart memory and fresh queue/heartbeat state; restart only when there are no current or unacknowledged tasks.
- [x] Verify fresh heartbeat, service readiness, OCR inference and post-restart memory.

No dependency upgrades or global concurrency changes. Do not claim the entire historical memory increase is explained by this patch; validate the resulting process separately.
