(* University of Florida *)
(* Author: Bohdan Purtell *)
(* Module: "platform/target.mli" *)
(* Where a design is going: a declared harness and technology pair, and the resolved
   target that pair stands for.

   {v
   Chip_target.create ~target        declared pair; accepted as is, the pair is NOT checked
   Chip_target.elaborate             uses [technology] for resource selection only
   Chip_target.elaborate_for_flow    Elaboration.Resolved.resolve -> Target.resolve
                                 -> unsupported pair or bad inputs: Error
                                 -> Resolved.t: geometry, floorplan, pins, layers, views
   Bundle                        writes the Resolved.t references into the manifest
   v}

   Resolution is deterministic: the same pair and inputs always give an equal
   {!Resolved.t}. Only Tiny Tapeout [T6x4] with {!Technology.ihp_sg13cmos5l} resolves;
   every other pair is an error, even ones {!Chip_target.elaborate} accepts.

   A resolved target records references to external files by revision, relative path and
   (for the floorplan) hash. It does NOT open, fetch or hash any of them; checking them
   against a real support-tools checkout and PDK is P4 preflight. It does NOT check the
   design either; the TT top-level interface, metadata, pinout and timing are checked by
   {!Elaboration.Resolved.resolve}.
*)

open! Core

(** A declaration, NOT a resolved target: any harness can be paired with any technology
    here. Use {!resolve}, or {!Chip_target.elaborate_for_flow}, to find out whether the pair
    is supported. *)
type t =
  { harness : Harness.t
  (** What the design is packaged into, and what its top level must look like. *)
  ; technology : Resource.Technology.t (** What the design is built out of. *)
  }
[@@deriving sexp_of]

(** An axis aligned rectangle in micrometres, lower left to upper right. Not private, so
    nothing guarantees [x_max > x_min] outside {!resolve}. *)
module Rectangle_um : sig
  type t =
    { x_min : float
    ; y_min : float
    ; x_max : float
    ; y_max : float
    }
  [@@deriving sexp_of]
end

(** One external file a resolved target points at, pinned to a revision. A reference says
    where a file should be; it is NOT evidence that the file exists or has that hash. *)
module Reference : sig
  module Source : sig
    type t =
      | Support_tools (** The TinyTapeout tt-support-tools checkout. *)
      | Pdk (** The IHP Open PDK checkout. *)
    [@@deriving sexp_of]
  end

  module Role : sig
    type t =
      | Floorplan_def (** The TT block DEF template the floorplan starts from. *)
      | Technology_view of Resource.Technology.Cmos5l.View.Role.t
      (** One CMOS5L standard-cell view, with its own view role. *)
    [@@deriving sexp_of]
  end

  type t =
    { source : Source.t (** Which checkout [path] and [revision] belong to. *)
    ; revision : string (** Full lowercase Git commit of that checkout. *)
    ; role : Role.t (** What the file is for. *)
    ; path : string (** Relative to the root of the [source] checkout, never absolute. *)
    ; sha256 : string option
    (** Expected content hash; [Some] only for the floorplan, PDK views are pinned by
        [revision] alone. *)
    ; corner : string option
    (** Operating corner; [Some] only for the standard-cell Liberty view. *)
    }
  [@@deriving sexp_of]
end

(** The externally pinned facts {!resolve} needs that the harness and technology do not
    say. Transparent, so a caller pinning other revisions starts from
    {!reference_cmos5l_6x4} and overrides fields with [{ ... with pdk_revision = ... }].

    {!resolve} checks the shape of every field, NOT whether the revisions actually contain
    that floorplan, hash, die area or those views; a wrong hash or die area resolves fine
    and is first caught by P4 preflight. *)
module Inputs : sig
  type t =
    { support_tools_revision : string
    (** Full lowercase 40 digit commit of tt-support-tools. *)
    ; pdk_revision : string (** Full lowercase 40 digit commit of the IHP Open PDK. *)
    ; floorplan_path : string
    (** Must be exactly ["tech/ihp-sg13cmos5l/def/tt_block_6x4_pgvdd.def"]; other
        revisions can be pinned, other DEF files cannot. *)
    ; floorplan_sha256 : string (** Lowercase 64 digit hex SHA-256 of the DEF. *)
    ; die_area_um : Rectangle_um.t
    (** Finite, with a positive width and height; should match the pinned
        [tile_sizes.yaml]. *)
    ; technology_views : Resource.Technology.Cmos5l.View.t list
    (** One view per {!Technology.Cmos5l.View.Role.t}, each at a relative path; the
        Liberty view at {!Technology.Cmos5l.corner}, and no other view with a corner. *)
    ; constraints : Resource.Technology.Cmos5l.Constraints.t
    (** The timing and drive environment the emitted SDC applies, defaulting to
        {!Technology.Cmos5l.constraints}. This is the only supported way to change one:
        every LibreLane key that would otherwise set it is protected, so it cannot be
        reached by a raw {!Flow.Librelane.Override.t}. Checked for a nameable driving
        cell, a finite nonnegative load, uncertainty and transition, a positive fanout and
        a derate below 100%; whether the driving cell exists in the Liberty is NOT checked
        here. *)
    }
  [@@deriving sexp_of]

  (** The adopted reference inputs, from the consumer's toolchain lock (2026-09-17); the
      default for {!resolve}. Revisions and sources are listed in
      docs/reference/target-reference.md. *)
  val reference_cmos5l_6x4 : t
end

(** Everything the flow needs to know about where the design is going, with every external
    file pinned.

    Careful: the type is NOT private. Only {!resolve} guarantees a checked value; do not
    build one by hand. *)
module Resolved : sig
  type t =
    { harness : Harness.t (** The declared harness, unchanged. *)
    ; technology_name : string (** The PDK name, ["ihp-sg13cmos5l"]. *)
    ; tiles : string (** The tile size as TT writes it, ["6x4"]. *)
    ; die_area_um : Rectangle_um.t (** Die bounds, from {!Inputs.die_area_um}. *)
    ; floorplan : Reference.t
    (** The DEF template, pinned by support-tools revision AND hash. *)
    ; harness_power_pins : string * string
    (** (power, ground) nets of the TT block: [("VPWR", "VGND")]. *)
    ; standard_cell_power_pins : string * string
    (** (power, ground) pins of the standard cells: [("VDD", "VSS")]. *)
    ; routing_layers : string list (** Layers the project may route on, lowest first. *)
    ; top_routing_layer : string (** The highest of [routing_layers]. *)
    ; corner : string (** The nominal timing corner. *)
    ; views : Reference.t list
    (** Every PDK view, in the order {!Inputs.technology_views} lists them. *)
    ; constraints : Resource.Technology.Cmos5l.Constraints.t
    (** The checked timing and drive environment, from {!Inputs.constraints}. *)
    }
  [@@deriving sexp_of]
end

(** [resolve ~inputs { harness = Tiny_tapeout { tiles = T6x4 }; technology }]

    Returns an error naming the harness and technology name if the pair is not TT [T6x4]
    with {!Technology.ihp_sg13cmos5l}; [inputs] are not looked at in that case. For the
    supported pair, [inputs] (default {!Inputs.reference_cmos5l_6x4}) are checked as
    documented on {!Inputs.t}, and every problem found is reported in one combined error.

    Reads no files and fetches no PDK. *)
val resolve : ?inputs:Inputs.t -> t -> Resolved.t Or_error.t
