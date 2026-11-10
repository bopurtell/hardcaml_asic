(** Bounded retained memory-1rw-latency1-v1 evidence. No simulation, publication,
    discovery or authenticated-execution claim. See pt8-workload-handoff.md. *)
val inspect : string -> (Evidence_json.t, string) result
val snapshot : string -> (Evidence_json.t, string) result
val recheck : string -> expected_digest:string -> (unit, string) result
