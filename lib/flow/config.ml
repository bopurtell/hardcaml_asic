(* University of Florida *)
(* Author: Bohdan Purtell *)
(* Module: "flow/config.ml" *)
(* Source implementation of flow selection. A flow is the project's answer to "which tool
   flow builds this design, how far does it go, and with which settings set by hand?";

   Project.create takes it as [~flow], usually built with [librelane], and checks it
   through [validate] alongside the declaration checks; Elaborated_project retains it;
   Elaboration.Resolved then picks the technology views the [operation] needs and merges
   the LibreLane overrides over the settings it derives; Bundle records the operation in
   the manifest.

   The flow is selected separately from the harness and technology in Target on purpose: a
   technology does not imply one tool flow, and an adapter does not define resource
   behavior (see docs/architecture.md section 3, "Flow selection and compatibility").

   It does NOT know which LibreLane keys are protected or what their defaults are; that is
   Elaboration.Resolved's job. The overrides are a LibreLane representation, NOT the core
   project model (see docs/architecture.md section 6).
*)

open! Core

[@@@ocamlformat "disable"]

(* How far the flow is asked to take the design;

   Synthesis : synthesis to a gate netlist; Elaboration.Resolved.required_views asks the
               technology for the PDK configuration and the standard cell Liberty and
               Verilog views;
   Hardening : synthesis plus place and route; additionally needs the technology LEF and
               the standard cell LEF and GDS views;
*)
module Operation = struct
  type t =
    | Synthesis
    | Hardening
  [@@deriving compare, equal, sexp_of]
end

(* Everything specific to the LibreLane adapter; *)
module Librelane = struct
  (* One raw LibreLane configuration assignment; the escape hatch for settings that have
     no typed API;

     key    : LibreLane configuration variable name, "PL_TARGET_DENSITY_PCT" for example;
              must be an uppercase identifier, see [validate];
     value  : the JSON value written for [key]; must be strict JSON, see [validate];
     reason : why the setting is needed; must be nonblank, and is carried into the
              resolved settings as that setting's provenance;

     Careful: [validate] only checks the shape of [key], not that LibreLane knows it. A
     typo such as "PL_TARGET_DENSTY_PCT" validates fine and ends up in the flow
     configuration as is; nothing in this library catches it. A key that collides with a
     derived setting ("DIE_AREA", or anything containing "SDC") is caught, but later, by
     Elaboration.Resolved before emission.
  *)
  module Override = struct
    type t =
      { key    : string
      ; value  : Yojson.Safe.t
      ; reason : string
      }

    (* Written by hand since Yojson.Safe.t has no sexp_of; [value] is rendered as its JSON
       text, so an error shows ((key PL_TARGET_DENSITY_PCT) (value 60) (reason "")); *)
    let sexp_of_t { key; value; reason } =
      [%sexp
        { key    : string
        ; value  = (Yojson.Safe.to_string value : string)
        ; reason : string
        }]
    ;;
  end

  (* The LibreLane adapter's settings;

     overrides : assignments set by hand, applied over Elaboration.Resolved's derived
                 settings; their order does not matter, since [validate] rejects a key set
                 twice and the resolved settings are sorted by key;
  *)
  type t =
    { overrides : Override.t list
    ; macros : Macro_intent.t list
    }
  [@@deriving sexp_of]

  (* Check the overrides at declaration time; returns Or_error rather than raising since
     Project.create folds it, through the outer [validate], into one combined declaration
     error;

     invalid   : every override with a malformed key, a value that is not strict JSON, or
                 a blank reason, reported together;
     duplicate : a key set more than once; only the first repeat found is reported;

     Both checks run even when the other fails, so one call reports both problems.

     Protected keys are NOT checked here; whether a key may be overridden depends on what
     Elaboration.Resolved derives, so that conflict check lives there.

     Duplicates compare [key] only; two overrides of the same key with different values
     are still a duplicate, since LibreLane can only take one of them.
     -> see expect tests (test/test_lifecycle.ml, test/test_resolved_flow.ml)
  *)
  let validate { overrides; macros } =

    let blank s = String.is_empty (String.strip s) in

    (* Is the key an uppercase identifier, "[A-Z_][A-Z0-9_]*"? This also rules out stray
       whitespace, such as "CLOCK_PERIOD "; *)
    let valid_key key =
      let letter c = Char.(c >= 'A' && c <= 'Z') in
      let digit c = Char.(c >= '0' && c <= '9') in
      match String.to_list key with
      | [] -> false
      | first :: rest ->
        (letter first || Char.equal first '_')
        && List.for_all rest ~f:(fun c -> letter c || digit c || Char.equal c '_')
    in

    (* Is the value something a strict JSON reader accepts? Yojson.Safe.t is wider than
       JSON, so walk it and reject the extensions; *)
    let rec strict_json : Yojson.Safe.t -> bool = function

      (* Scalars that are always valid JSON *)
      | `Null | `Bool _ | `Int _ | `String _ -> true

      (* NaN and the infinities have no JSON spelling; Yojson writes them as NaN or
         Infinity, which a strict JSON reader rejects *)
      | `Float value -> Float.is_finite value

      (* An array is valid when every element is *)
      | `List values -> List.for_all values ~f:strict_json

      (* An object must not repeat a field name, since readers disagree on which copy
         wins, and every field value must be valid *)
      | `Assoc fields ->
        Option.is_none
          (List.find_a_dup fields ~compare:(fun (a, _) (b, _) -> String.compare a b))
        && List.for_all fields ~f:(fun (_, value) -> strict_json value)

      (* Tuple and Variant have no JSON spelling; Intlit holds an integer too big for an
         int as a string that Yojson prints verbatim, and nothing checks that string, so
         [`Intlit "abc"] would land in the config as is; no LibreLane setting needs one
      *)
      | `Intlit _ | `Tuple _ | `Variant _ -> false
    in

    (* Who is invalid out of the overrides? *)
    let invalid =
      List.filter overrides ~f:(fun (o : Override.t) ->
        not (valid_key o.key) || blank o.reason || not (strict_json o.value))
    in

    (* Is any key set twice? *)
    let duplicate =
      List.find_a_dup overrides ~compare:(fun (a : Override.t) b ->
        String.compare a.key b.key)
    in

    (* Checking with the above in a list format, fold any errors into the result;

       TODO (future fix): the duplicate message does not follow
       the structured diagnostic convention, which wants "what went wrong; how to fix it".
       Something like "LibreLane override key set more than once; keep one override per
       key". Changing it changes expect test output (test/test_lifecycle.ml), so update
       that in the same commit;
    *)
    Or_error.combine_errors_unit
      [ (match List.find_a_dup macros ~compare:(fun a b -> Resource.Id.compare a.resource b.resource) with
         | None -> Ok ()
         | Some intent -> Or_error.error_s [%message "duplicate macro physical intent" (intent : Macro_intent.t)])
      ; (match invalid with
         | [] -> Ok ()
         | _ :: _ ->
           Or_error.error_s
             [%message
               "LibreLane overrides need an uppercase key, strict JSON value, and \
                nonempty reason"
                 (invalid : Override.t list)])

      ; (match duplicate with
         | None -> Ok ()
         | Some duplicate ->
           Or_error.error_s
             [%message
               "LibreLane override key set more than once" (duplicate : Override.t)])
      ]
  ;;
end

(* The selected flow adapter;

   Librelane s : the LibreLane flow, with its adapter specific settings [s];

   A variant with one constructor on purpose: future adapters (a commercial flow, for
   example) become new constructors carrying their own settings, instead of fields bolted
   onto the LibreLane settings.
*)
module Adapter = struct
  type t = Librelane of Librelane.t [@@deriving sexp_of]
end

(* The flow selection itself;

   adapter   : which tool flow builds the design, with that flow's own settings;
   operation : how far the adapter is asked to take it;

   The record is left transparent (there is no mli) so it can be built by hand, which
   means nothing forces [validate] onto it; Project.create is what runs it, before a flow
   ever reaches an Elaborated_project -> see test/fixture.ml.
*)
type t =
  { adapter   : Adapter.t
  ; operation : Operation.t
  }
[@@deriving sexp_of]

(* Select the LibreLane adapter for [operation]; no overrides is the usual case, as in
   [librelane Hardening] -> see examples/memory_example.ml; *)
let librelane ?(overrides = []) ?(macros = []) operation =
  { adapter = Librelane { overrides; macros }; operation }
;;

(* Check the flow at declaration time by handing off to the selected adapter's validator;
   returns Or_error since Project.create combines it with its other declaration checks;

   [operation] needs no check here; whether the technology has the views it needs is
   Elaboration.Resolved.validate_capabilities's job, once the target is resolved.

   An exhaustive match with no wildcard on purpose: a new Adapter constructor fails to
   compile here until someone decides how its settings are validated.
*)
let validate t =
  match t.adapter with
  | Librelane settings -> Librelane.validate settings
;;
[@@@ocamlformat "enable"]
