(* University of Florida *)
(* Author: Bohdan Purtell *)
(* Module: "resource/policy.mli" *)
(* Project policy for resource implementation selection.

   A policy is a default requirement plus rules scoped to one instance or to a scope
   subtree. It controls selection; it is not an inventory of resources. Precedence is
   independent of rule and registration order:

   1. an [Instance] rule naming the resource;
   2. the deepest matching [Subtree] rule;
   3. the default.

   Repeating a selector is ambiguous and rejected. A resource with no applicable
   requirement fails registration: there is no implicit implementation choice. A rule that
   never applies fails finalization: either it matches no registered resource (most likely
   a misspelt identity), or every resource it matches is decided by a higher-precedence
   rule. *)

open! Core

module Requirement : sig
  type t =
    | Flops (** Explicitly select synthesizable flop storage. *)
    | Exact (** Require an exact technology mapping. *)
    | Exact_or_flop_fallback
    (** Use an exact mapping when one exists; otherwise flops, recorded as fallback. *)
  [@@deriving compare, equal, sexp_of]
end

module Selector : sig
  type t = private
    | Instance of Id.t
    | Subtree of string list
  [@@deriving compare, equal, sexp_of]

  (** [instance_exn "a/b/name"] *)
  val instance_exn : string -> t

  (** [subtree_exn "a/b"] matches resources registered in scope [a/b] or below it. *)
  val subtree_exn : string -> t

  val matches : t -> Id.t -> bool
end

module Rule : sig
  type t =
    { selector : Selector.t
    ; requirement : Requirement.t
    }
  [@@deriving sexp_of]
end

(** Which part of the policy produced a requirement. *)
module Applied : sig
  type t =
    | Default
    | Rule of Selector.t
  [@@deriving compare, equal, sexp_of]
end

type t [@@deriving sexp_of]

(** [macro_limits] caps the number of selections of each named macro per elaboration,
    in registration order across scopes. Unlisted macros are unlimited. A limit of zero
    disables a macro. Duplicate names, blank names and negative limits are rejected. *)
val create
  :  ?default:Requirement.t
  -> ?macro_limits:(string * int) list
  -> Rule.t list
  -> t Or_error.t

val macro_limit : t -> string -> int option
val limited_macro_names : t -> string list
val rules : t -> Rule.t list
val lookup : t -> Id.t -> (Requirement.t * Applied.t) Or_error.t
