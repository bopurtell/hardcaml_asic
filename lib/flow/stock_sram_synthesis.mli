(** Provider-owned black box shared by rendering and pure named-plan admission. *)
val macro_stub : string

(** Reconstruct the generated settings for the schema-1, unplaced stock SRAM synthesis
    contract. Checked inside [Plan_json.decode]; performs no I/O. *)
val generated
  :  manifest:Yojson.Safe.t
  -> contract:Yojson.Safe.t
  -> (string * Yojson.Safe.t) list
