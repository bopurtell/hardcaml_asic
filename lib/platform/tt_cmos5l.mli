(* University of Florida *)
(* Author: Bohdan Purtell *)
(* Module: "platform/tt_cmos5l.mli" *)
(* The Tiny Tapeout CMOS5L template: the parts of a TT declaration that are the same in
   every design, so a project states only what is its own.

   Three independent pieces live here. A design can take any one of them and write the
   others by hand:

   {v
   I and O              the fixed TT user-module ports, as Hardcaml interfaces
   overrides            the pinned template's LibreLane settings, as library defaults
   synchronised_reset   the TT wrapper's reset idiom, with [gate] for the pads
   v}

   {!I} and {!O} are exactly the ports {!Elaboration.Resolved} checks a design against, so
   a design built on them cannot fail that check. {!overrides} returns the template's raw
   LibreLane settings as {!Flow.Librelane.Override.t} values, each carrying a [reason]
   that says what the setting does and where the value came from; the consumer's own
   overrides are merged over them by key.

   It does NOT decide anything about a design: the top module name, geometry, clock,
   pinout, timing, resource policy and source inputs are the project's. It does NOT
   relax the protected settings either; {!Elaboration.Resolved} still refuses an override
   of CLOCK_PERIOD, VERILOG_FILES, DIE_AREA and the rest, whether the override came from a
   consumer or from {!overrides}. Nothing here is a functor or a module type to satisfy:
   a design picks up the ports with two aliases and instantiates its core itself, so a
   design that needs different reset behaviour simply does not call
   {!synchronised_reset}. *)

open! Core
open! Hardcaml

(** The ttihp-verilog-template commit every {!template_overrides} value was taken from,
    as a full lowercase 40 digit Git commit. Each default's [reason] names it too, so a
    setting copied out of a manifest still says where it came from. *)
val template_revision : string

(** The Tiny Tapeout user-module inputs, in the order the TT template declares them.
    Fixed by Tiny Tapeout: {!Elaboration.Resolved} refuses to emit a design whose top
    level has any other input port, width or direction. *)
module I : sig
  type 'a t =
    { ui_in : 'a [@bits 8] (** Dedicated input pins. *)
    ; uio_in : 'a [@bits 8] (** Bidirectional pins, as seen by the design. *)
    ; ena : 'a (** High while this design is the one selected on the die. *)
    ; clk : 'a (** The one primary clock; {!Elaboration.Resolved} requires this name. *)
    ; rst_n : 'a (** Active low reset from the harness, NOT synchronised. *)
    }
  [@@deriving hardcaml]
end

(** The Tiny Tapeout user-module outputs, in the order the TT template declares them.
    Fixed by Tiny Tapeout, exactly as {!I} is. *)
module O : sig
  type 'a t =
    { uo_out : 'a [@bits 8] (** Dedicated output pins. *)
    ; uio_out : 'a [@bits 8] (** Bidirectional pins driven by the design. *)
    ; uio_oe : 'a [@bits 8] (** Per bit output enable for [uio_out]; 1 drives the pin. *)
    }
  [@@deriving hardcaml]
end

(** The template's twenty LibreLane settings, in the order the template's
    [src/config.json] writes them, each with its own [reason].

    Exposed so a consumer can read what it is getting without running the flow; build the
    list a project passes with {!overrides} rather than editing a copy of this one. The
    template's other settings are not here because the declaration derives them:
    CLOCK_PORT and CLOCK_PERIOD from the clock, FP_SIZING and RUN_LINTER as
    {!Elaboration.Resolved} defaults. *)
val template_overrides : Flow.Config.Librelane.Override.t list

(** [overrides ()] is {!template_overrides}; [overrides ~extra ()] is
    {!template_overrides} with every default [extra] names replaced by [extra]'s entry,
    and [extra]'s remaining entries appended; [overrides ~without ()] drops the named
    defaults instead of replacing them, which is how a project asks LibreLane for its own
    value of a setting the template pins.

    Order is not meaningful: {!Elaboration.Resolved} sorts the resolved settings by key.

    Raises, naming the keys, if [without] names a key that is not a template default (so
    a typo cannot silently leave the default in place), or if [without] and [extra] name
    the same key. A key repeated within [extra] is NOT caught here; {!Flow.Librelane}'s
    own validation reports it when {!Chip_target.create} runs. *)
val overrides
  :  ?without:string list
  -> ?extra:Flow.Config.Librelane.Override.t list
  -> unit
  -> Flow.Config.Librelane.Override.t list

(** [synchronised_reset ~clock ~rst_n ()]

    The TT wrapper's reset idiom, as an active high reset for the design's core:
    asserted asynchronously as soon as [rst_n] goes low, released [stages] rising edges
    of [clock] after [rst_n] goes high again. [rst_n] arrives from the harness
    unsynchronised, so a core released straight from it can be released mid metastable
    window in one flop and not another.

    [stages] defaults to 2 and must be at least 2; raises, naming the value, otherwise. A
    one stage chain would resynchronise nothing, which is the mistake worth catching
    rather than accepting.

    Opt in: a design that wants a different reset (synchronous only, or none) ignores
    this and builds its own. *)
val synchronised_reset
  :  ?stages:int
  -> clock:Signal.t
  -> rst_n:Signal.t
  -> unit
  -> Signal.t

(** [gate ~enable signal] is [signal] while [enable] is high and zero otherwise, the
    other half of the TT wrapper idiom: a design that is not selected, or is in reset,
    must not drive the pins it shares with every other design on the die.

    Left as one gate per output rather than one call gating all of {!O}, because the
    three outputs are not always gated on the same condition; the reference consumer
    holds [uo_out] low through its core reset as well. [enable] must be one bit. *)
val gate : enable:Signal.t -> Signal.t -> Signal.t
