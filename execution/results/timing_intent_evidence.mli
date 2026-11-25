(** Bounded, read-only inspection of a standalone retained synthesized-netlist STA
    audit. Does not launch tools, discover runs, write summaries, or qualify normal
    flow/post-PNR integration. All input and report bytes are retained and hash
    checked. Successful exception commands are distinguished from native
    path-effect coverage; unavailable coverage never yields a passing verdict.
    Checks the supported schema-1 bundle envelope's structural fields and selected
    cross-links (including RTL source roles, runtime policy and floorplan reference),
    not re-resolution of the project or target/base-clock-policy compatibility.
    Hashes the entire
    SDC against its identity-bound file entry; Timing intent Tcl suffix replay is additional,
    not reconstruction of the whole base-SDC policy. Log processing retains only
    bounded receipt/wire state (65,536 lines, 64 KiB/line, 1,028 events/2 MiB wire).

    Schema 1 retains current SDC serializer suffix replay. Schema 2 instead binds
    the retained versioned native producer to its identity-covered bundle member,
    with no current producer or SDC renderer replay. It interprets exact stored
    exception scope and complete returned diagnostic groups/native endpoints;
    exhaustive exception effect and overall coverage remain unavailable.
    Schema 2 requires native profile/data version 2 with category-bound raw
    diagnostics and a leading native current_design matching the manifest top.
     Older native-v1 schema-2 captures reject explicitly as unsupported and require
     recapture; they are never relabelled as category-bound profile-2 evidence.

     Schema 3 is explicitly extracted-netlist/post_pnr scoped, requires the
     identity-covered physical policy and SPEF receipt input, and retains native
     annotation/clock evidence. Coarse provenance and timing/electrical verdicts
     belong to Timing_intent_flow, not this inner native-scope audit reader.

    The schema-specific producer contracts are enforced by this reader. Sources
    must not be mutated concurrently with inspection. Receipts are source-replayed
    producer assertions, not authenticated execution attestations. *)
val inspect : directory:string -> unit -> (Evidence_json.t, string) result
val validate_qualification : Evidence_json.t -> unit
