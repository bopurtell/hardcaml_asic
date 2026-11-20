(* University of Florida *)
(* Author: Bohdan Purtell *)
(* Module: "elaboration/resolved.mli" *)
(* Validated TT/LibreLane resolution results for Bundle and design observers.

    Only [resolve], also called by Chip_target.elaborate_for_flow, constructs a result.
   Private records preserve field reads and pattern matching but forbid construction
   and record updates. There is no unchecked constructor or deserializer.

   Resolution checks the target first, then combines wrapper port, submission metadata,
   pinout and implementation capability diagnostics in one stage. Timing is checked
   next, followed by settings and protected overrides. It relies on Elaborated_project's
   declaration/elaboration invariants; it does not freeze Hardcaml circuit internals
   or verify external target files. Bundle owns emission.
*)

open! Core

(** A resolved configuration assignment and its provenance. JSON values use immutable
    lists and strings; inspecting a value cannot mutate the stored setting. *)
module Setting : sig
  module Owner : sig
    type t =
      | Design (** Elaborated design fact. *)
      | Target (** Resolved target fact. *)
      | Clock (** Resolved primary clock fact. *)
      | Default (** Adapter default, replaceable by an override. *)
      | Override of string (** Explicit override, carrying its declared reason. *)
      | Methodology of Hardcaml_asic_flow_domain.Parameter.origin (** Checked catalog layer. *)
    [@@deriving sexp_of]
  end

  type t = private
    { key : string (** LibreLane variable name. *)
    ; value : Yojson.Safe.t (** Value emitted in the configuration. *)
    ; owner : Owner.t (** Source recorded in the manifest. *)
    }
  [@@deriving sexp_of]
end

(** Actual typed configuration after project/catalog merging. Project contributions
    retain their original [Override reason] owner, including template attribution.
    Histories run oldest to newest and exclude the winner. No backend documentation
    default is injected. These private immutable records cannot forge resolution. *)
module Effective_parameter : sig
  type contribution = private
    { assignment : Hardcaml_asic_flow_domain.Parameter.assignment
    ; owner : Setting.Owner.t
    }
  type t = private
    { winning : contribution
    ; overridden : contribution list
    }
end

(** Exact implementation facts checked against registered resources and Hardcaml RTL. *)
module Macro : sig
  type t = private
    { resource : Resource.Id.t
    ; name : string
    ; instance : string
    ; collateral : Resource.Technology.Collateral.t list
    ; intent : Flow.Macro_intent.t option
    }
end

(** A checked delay, converted to nanoseconds. *)
module Delay_ns : sig
  type t = private
    { port : string (** Existing nonclock port in the declared direction. *)
    ; minimum : float (** Finite earliest change, no greater than [maximum]. *)
    ; maximum : float (** Finite, nonnegative latest settle. *)
    }
  [@@deriving sexp_of]
end

(** Read-only resolution facts. Subordinate target and clock records can be copied,
    but copies cannot be inserted into this private record. *)
type t = private
  { elaborated_project : Elaborated_project.t
    (** Implementation elaboration supplied to resolution, retained unchanged. *)
  ; target : Platform.Target.Resolved.t (** Checked, pinned target references. *)
  ; clock : Platform.Clock.t (** The sole primary clock, on [clk]. *)
  ; clock_period_ns : float (** Clock period converted to nanoseconds. *)
  ; clock_hz : float (** [1e9 /. clock_period_ns]. *)
  ; input_delays_ns : Delay_ns.t list (** Input delays sorted by port. *)
  ; output_delays_ns : Delay_ns.t list (** Output delays sorted by port. *)
  ; settings : Setting.t list (** Merged assignments sorted by unique key. *)
  ; methodology : Hardcaml_asic_flow_domain.Catalog.resolved option (** Original checked snapshot. *)
  ; effective_parameters : Effective_parameter.t list
    (** Configurable, explicitly assigned keys in backend-key order. Empty without
        methodology. Inherits checked project values when the catalog is silent.
        Project values are validated even when replaced by catalog assignments. *)
   ; macros : Macro.t list (** Checked exact SRAM instances. *)
   ; physical_plan : Macro_physical_plan.t option
     (** Complete fresh-hardening policy, only for exact R0 SRAMs with common halo.
          Runtime must validate actual geometry and execute the declared checking profile. *)
   ; timing_intent : Timing_intent.t option
     (** Validated scalar timing declarations, not mapped-tool resolution facts. *)
  }

(** Resolve an implementation elaboration; defaults to {!Target.Inputs.reference_cmos5l_6x4}.
     Returns an error on unsupported targets, missing capabilities, invalid wrapper,
     metadata, pinout or timing, or overrides of protected facts. No files are read.
     A failure stops subsequent validation stages; no partial result is returned.

     With [methodology], precedence is provider-derived defaults, existing project
     assignments, methodology defaults, profile, then request. All inherited values
     for configurable keys must pass the strict typed domain even if replaced.
     Unconfigurable project settings retain their raw values and ownership; catalog
     assignments have already been gated by the checked snapshot. Protected facts
     remain authoritative. [effective_parameters] records actual values and complete
     replaced project/catalog history; unspecified runtime defaults remain absent.
     The snapshot's endpoint/stages are retained for subsequent plan construction;
     this seam does not replace the project's [Flow.t] or its execution recipe. *)
val resolve
  :  ?inputs:Platform.Target.Inputs.t
  -> ?methodology:Hardcaml_asic_flow_domain.Catalog.resolved
  -> Elaborated_project.t
   -> t Or_error.t

(** Pure checked replay of the exact typed project/catalog merge for plan readers.
    Project values must be configurable, unique typed assignments with nonblank
    original reasons. Uses the same merge as [resolve], never runtime defaults. *)
val resolve_effective_parameters
  :  methodology:Hardcaml_asic_flow_domain.Catalog.resolved
  -> project_assignments:(Hardcaml_asic_flow_domain.Parameter.assignment * string) list
  -> Effective_parameter.t list Or_error.t

(** Required TT input names and widths, including phantom inputs. *)
val expected_inputs : (string * int) list

(** Required TT output names and widths. *)
val expected_outputs : (string * int) list

(** Whether a LibreLane key is reserved for generated facts, including any SDC key. *)
val protected_key : string -> bool

(** Check implementation mode, required technology views and initial resource source support.
    Full stock SRAM collateral and RTL-instance checking occurs during [resolve].
    Useful for capability diagnostics; success does not construct a resolved flow. *)
val validate_capabilities : Elaborated_project.t -> Platform.Target.Resolved.t -> unit Or_error.t

(** Adapter-owned exact stock collateral, shared with deterministic emission. *)
val stock_macro : string
val macro_corners : (string * string) list
val macro_path : string -> string -> string
