(** Project-owned directory of chip entry points. The manifest does not load
    designs, discover executable files, or own the target's flow catalog. *)
open! Core

type entry = private { id : string; executable : string }
type t

val filename : string
val of_json : Yojson.Safe.t -> t Or_error.t
val to_json : t -> Yojson.Safe.t
val entry_to_json : entry -> Yojson.Safe.t
val load : string -> t Or_error.t
val entries : t -> entry list
val default_target : t -> string

(** Only omission selects the default. Explicit IDs never fall back. *)
val select : t -> id:string option -> entry Or_error.t
