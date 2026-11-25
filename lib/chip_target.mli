(* University of Florida *)
(* Author: Bohdan Purtell *)
(** An immutable chip target declaration and its elaboration lifecycle.

   {v
   Chip_target.create         validate the declaration (names, metadata, clocks,
                              policy, adapter settings)
   Chip_target.elaborate ~mode fresh Elaboration.Context
                              -> run the design constructor; resources register and
                                 select implementations (selection failures raise here)
                              -> finalize: validate collected state, close the context
                              -> immutable Elaborated_project.t
   v}

   Elaboration never invokes an external tool. Each call is independent: a failed
   elaboration leaves nothing behind for the next one. [elaborate_for_flow]
   applies target, interface, constraint, and adapter validation to an
   implementation elaboration before flow emission.
*)

open! Core
open! Hardcaml

(* we're saying that a Design module implements the following items;

   It has a module I and O that implement the capabilities of Hardcaml.Interface;

   It has a "name" member and a function "create" that follows a certain signature.
   Therefore anything that implements these functions, and has the members can be counted
   on as a "Design".
*)
module type Design = sig
  module I : Interface.S
  module O : Interface.S

  (** Top-level module name. *)
  val name : string

  (* A design must have a create function; different from standard is the elaboration
     context that is required for ASIC items. This may be for simulation though, or even
     emultaion, meaning an elaboration context varies by target we're shooting.
  *)
  val create : Elaboration.Context.t -> Signal.t I.t -> Signal.t O.t
end

(** One independently elaborated chip in the enclosing Dune project. *)
type t

[@@@ocamlformat "disable"]
(* Chip_target.create is a function that has members:
  name      : string
  metadata  : Elaboration.Metadata.t
  design    : Design (a module that implements the properties of the Design signature)
  target    : asic? fpga? sim? emulate?
  flow      : tt? cadence? synopsys?
  clocks    : clock data; what speeds, who exists, where they are
  policy    : per-instance or per-subtree requirement (Flops, Exact, Exact_or_flop_fallback); exact contracts only, never rounded or composed; flops are the only fallback
  unit      : positional for optional boundaries

  -> Or_error.t
      we return an error type
*)
val create
  :  name:string
  -> metadata:Elaboration.Metadata.t
  -> design:(module Design)
  -> target:Platform.Target.t
  -> flow:Flow.Config.t
  -> ?clocks:Platform.Clock.t list
  -> ?pinout:Platform.Pinout.t
  -> ?timing:Platform.Timing.t
  -> ?source_inputs:string list
  -> policy:Resource.Policy.t
  -> unit
  -> t Or_error.t

(** Execution recipes are declared separately with [Flow.Recipe]. They do not
    alter [create]'s flow selection, bundle schema, or input-bundle identity.
    Emit a bundle here, then pass the recipe JSON to the provider [stages --plan]
    command; see [docs/guides/ocaml-stage-recipes.md]. *)

(* Of a type t, retrieve a string to be used as a name; *)
val name : t -> string

(** Calls the factory exactly once with this checked immutable assignment, then
    attaches it to the returned chip target. Factory errors and exceptions return an
    error; existing values are unchanged. Rejects any prior attachment, including
    an equal assignment. This does not elaborate hardware or prove parameter use:
    consumer adapters must validate supported tuples and test semantic conformance. *)
val of_variant
  :  variant:Hardware_variant.t
  -> create:(Hardware_variant.t -> t Or_error.t)
  -> t Or_error.t

val hardware_variant : t -> Hardware_variant.t option

(** Declared source inventory, unchanged; defaults to []. Paths are relative to the
    source root supplied to [emit_bundle]. Creation does not read or validate files. *)
val source_inputs : t -> string list

(** Local declaration facts, including the packed Design.name, without constructing
    a circuit, resolving a flow, or reading files, Git or a PDK. This is not a
    Silver driver or observation protocol. *)
val describe : t -> Sexp.t

(** Elaborate the design in the selected mode, retaining its intent and resources.
    Returns an elaborated Hardcaml circuit, not a synthesis result. *)
val elaborate : t -> mode:Elaboration.Mode.t -> Elaboration.Elaborated_project.t Or_error.t

(** First-class preparation, independent of bundle publication and physical stages.
    Observes declared sources before and after, elaborates Implementation exactly
    once, resolves once, and hashes actual RTL in memory. No Simulation elaboration,
    Bundle.render, EDA, or PDK reads. Source changes reject success. These declared
    file observations do not prove compiled-source consistency or a P6 stamp.
    [on_phase] runs before each phase; its exceptions propagate to the caller. *)
val prepare_resources
  :  ?inputs:Platform.Target.Inputs.t
  -> ?methodology:Hardcaml_asic_flow_domain.Catalog.resolved
  -> ?catalog_inputs:string list
  -> ?on_phase:(Resource_preparation.Phase.t -> unit)
  -> t
  -> source_root:string
  -> (Resource_preparation.t, Resource_preparation.Failure.t) Result.t

(** Elaborate the selected implementation and validate the TT/LibreLane target,
    wrapper, timing, metadata, and configuration before emission. *)
(** [methodology] applies only checked configurable assignments, after chip target
    assignments, in methodology-default/profile/request order. Other target
    overrides and the target's flow remain authoritative. No declaration is
    mutated. Without it the legacy settings and emitted bytes are unchanged. *)
val elaborate_for_flow
  :  ?inputs:Platform.Target.Inputs.t
  -> ?methodology:Hardcaml_asic_flow_domain.Catalog.resolved
  -> t
  -> Elaboration.Resolved.t Or_error.t

(** The exact emission rendering sequence, without writing: reject empty inventory,
    elaborate Implementation and resolve, optionally elaborate Simulation (default
    true), then [Bundle.render]. Reads source inventory and Git as documented by
    [Bundle.render]; no EDA or PDK reads. The returned concrete bundle can be
    previewed with [Bundle.identity] and then passed directly to [Bundle.publish]
    or [Bundle.write]. Publish that same value after preview-hash comparison;
    calling an emission method would render again and re-observe sources/Git. *)
val render_bundle
  :  ?inputs:Platform.Target.Inputs.t
  -> ?methodology:Hardcaml_asic_flow_domain.Catalog.resolved
  -> ?include_simulation:bool
  -> t
  -> source_root:string
   -> Elaboration.Bundle.t Or_error.t

(** Inseparable checked resolution/render pair. Only [render_result] constructs
    it, by rendering exactly that resolution once. No external pair-linker. *)
module Rendered : sig
  type t
  val resolution : t -> Elaboration.Resolved.t
  val bundle : t -> Elaboration.Bundle.t
end

(** Same sequence as [render_bundle]. Additional [catalog_inputs] are consumer
    catalog declaration source files, relative to source_root, copied and hashed
    by Bundle alongside [source_inputs]. This is the public source-inventory seam
    for later consumer discovery. No output directories or tools are touched. *)
val render_result
  :  ?inputs:Platform.Target.Inputs.t
  -> ?methodology:Hardcaml_asic_flow_domain.Catalog.resolved
  -> ?include_simulation:bool
  -> ?catalog_inputs:string list
  -> t
  -> source_root:string
  -> Rendered.t Or_error.t

(** Emit using [elaborate_for_flow ?inputs], optional Simulation elaboration,
    [Bundle.render], then [Bundle.write], returning the written bundle.
    An empty source inventory fails before invoking the design; it does not prevent
    ordinary elaboration. [include_simulation] defaults to true. When false, no
    Simulation construction occurs and Bundle uses synthesis sources for simulation.

    This explicit I/O boundary reads declared source files and Git during rendering.
    Nothing is written before rendering succeeds. Inherits Bundle.write's new/empty
    output directory requirement and non-atomic writes: a write failure can leave a
    partial directory. No external flow tools or PDK reads are performed. *)
val emit_bundle
  :  ?inputs:Platform.Target.Inputs.t
  -> ?methodology:Hardcaml_asic_flow_domain.Catalog.resolved
  -> ?include_simulation:bool
  -> t
  -> source_root:string
  -> output_dir:string
  -> Elaboration.Bundle.t Or_error.t

(** The same rendering sequence and optional arguments as [emit_bundle], followed
    by [Bundle.publish]. Returns the rendered bundle and its absolute publication
    location/reuse result. An empty inventory fails before design construction;
    render failure leaves no store output. The caller supplies the store location
    (typically [<results-root>/bundles]); no root is inferred by the library.
    Inherits Bundle.publish's exact reuse, backing layout and atomic visibility. *)
val emit_bundle_to_store
  :  ?inputs:Platform.Target.Inputs.t
  -> ?methodology:Hardcaml_asic_flow_domain.Catalog.resolved
  -> ?include_simulation:bool
  -> t
  -> source_root:string
  -> store_dir:string
  -> (Elaboration.Bundle.t * Elaboration.Bundle.Publication.t) Or_error.t
[@@@ocamlformat "enable"]
