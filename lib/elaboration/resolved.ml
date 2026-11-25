(* University of Florida *)
(* Author: Bohdan Purtell *)
(* Module: "elaboration/resolved.ml" *)
(* Source implementation of the flow resolution boundary. A resolved flow answers
   "is this elaborated project ready for TT/LibreLane, and with which settings?";

   Project.elaborate_for_flow elaborates in Implementation mode and hands the project to
   [resolve]. That calls Target.resolve for the pinned target, checks the TT top-level
   interface, the metadata, Pinout.validate_tiny_tapeout and the technology capabilities,
   converts the clock and I/O delays to nanoseconds, and merges the Flow LibreLane
   overrides over the settings it derives; Bundle then renders the result into the SDC,
   info.yaml, config.json and the manifest.

   Every fact here comes from one Project declaration, its elaborated circuit and the
   Target inputs; nothing is read from disk.

   It does NOT emit anything, that is Bundle's job, and it does NOT open or hash the files
   the target references; Bundle only records their pinned hashes. It is TT/LibreLane
   specific on purpose: the wrapper port names, the single "clk" clock and the protected
   keys all belong to that pair. Stock exact SRAM synthesis preserves schema 1;
   Hardening requires a complete versioned R0 edge-access/checking plan. Actual
   geometry and physical acceptance remain execution checks, not resolution facts.
*)

open! Core
open! Hardcaml

[@@@ocamlformat "disable"]

(* One resolved LibreLane configuration assignment, with where it came from;

   key   : LibreLane configuration variable name, "CLOCK_PERIOD" for example;
   value : the JSON value written for [key] into config.json;
   owner : which fact produced the setting; the manifest records it as provenance;
*)
module Setting = struct
  (* Where a setting's value came from;

     Design     : the elaborated circuit, such as its top module name;
     Target     : the resolved Target, such as the die area or the floorplan template;
     Clock      : the one resolved primary clock;
     Default    : a value this module picks; the only derived settings an override may
                  replace, since every other one sits under a protected key;
     Override r : a Flow.Librelane.Override.t set by hand, with [r] its declared reason;
  *)
  module Owner = struct
    type t =
      | Design
      | Target
      | Clock
      | Default
      | Override of string
      | Methodology of Hardcaml_asic_flow_domain.Parameter.origin
    [@@deriving sexp_of]
  end

  type t =
    { key   : string
    ; value : Yojson.Safe.t
    ; owner : Owner.t
    }

  (* Written by hand since Yojson.Safe.t has no sexp_of; [value] is rendered as its JSON
     text, so a setting shows ((key PL_TARGET_DENSITY_PCT) (value 60) (owner ...)); *)
  let sexp_of_t { key; value; owner } =
    [%sexp
      { key   : string
      ; value = (Yojson.Safe.to_string value : string)
      ; owner : Owner.t
      }]
  ;;
end

module Effective_parameter = struct
  type contribution =
    { assignment : Hardcaml_asic_flow_domain.Parameter.assignment
    ; owner : Setting.Owner.t
    }
  type t =
    { winning : contribution
    ; overridden : contribution list
    }
end

(* One validated I/O delay in nanoseconds, the form Bundle.sdc renders;

   port    : the top-level port, already known to exist in the stated direction;
   minimum : the earliest change in ns; finite and no greater than [maximum];
   maximum : the latest settle in ns; finite and nonnegative;
*)
module Delay_ns = struct
  type t =
    { port    : string
    ; minimum : float
    ; maximum : float
    }
  [@@deriving sexp_of]
end

module Macro = struct
  type t =
    { resource : Resource.Id.t
    ; name : string
    ; instance : string
    ; collateral : Resource.Technology.Collateral.t list
    ; intent : Flow.Macro_intent.t option
    }
end

let stock_macro = "RM_IHPSG13_1P_256x16_c2_bm_bist"
let macro_corners =
  [ "nom_fast_1p32V_m40C", "fast_1p32V_m55C"
  ; "nom_slow_1p08V_125C", "slow_1p08V_125C"
  ; "nom_typ_1p20V_25C", "typ_1p20V_25C" ]
let macro_path directory suffix =
  "pdk_dir::libs.ref/sg13cmos5l_sram/" ^ directory ^ "/" ^ stock_macro ^ suffix

(* The resolved flow; the elaborated project plus checked and derived flow facts;

   elaborated_project : the Implementation elaboration it was resolved from, unchanged;
   target             : the resolved target from Target.resolve, with every file pinned;
   clock              : the one primary clock, already known to be on "clk";
   clock_period_ns    : [clock]'s period in nanoseconds, fractions kept;
   clock_hz           : 1e9 /. [clock_period_ns];
   input_delays_ns    : one Delay_ns.t per input delay, sorted by port;
   output_delays_ns   : one Delay_ns.t per output delay, sorted by port;
   settings           : derived settings merged with the overrides, sorted by key,
                        one per key;

   Private in the mli: only [resolve] can construct or update this record. Field reads
   remain public, but a caller cannot replace validated facts before Bundle consumes
   them. Setting and Delay_ns are private too; their JSON, lists and scalar fields are
   immutable, so inspection does not provide a mutation path.
*)
type t =
  { elaborated_project : Elaborated_project.t
  ; target             : Platform.Target.Resolved.t
  ; clock              : Platform.Clock.t
  ; clock_period_ns    : float
  ; clock_hz           : float
  ; input_delays_ns    : Delay_ns.t list
  ; output_delays_ns   : Delay_ns.t list
   ; settings           : Setting.t list
   ; methodology        : Hardcaml_asic_flow_domain.Catalog.resolved option
    ; effective_parameters : Effective_parameter.t list
     ; macros : Macro.t list
     ; physical_plan : Macro_physical_plan.t option
     ; timing_intent : Timing_intent.t option
  }

(* The name a port is known by at the top level; its first Hardcaml name;

   The "<unnamed>" branch only keeps this total; Hardcaml names every port of a circuit
   built from an interface, so it should never show up in an error;
*)
let port_name signal =
  match Signal.names signal with
  | name :: _ -> name
  | [] -> "<unnamed>"
;;

(* The circuit's top-level ports as (name, width), inputs then outputs;

   Phantom inputs are included on purpose: Hardcaml leaves an interface input the design
   never reads out of Circuit.inputs and records it as a phantom input instead, but it is
   still a port of the emitted wrapper. Without them a design that ignores "ena" would
   fail as missing it -> see expect tests (test/test_resolved_flow.ml);
*)
let ports circuit =

  (* (name, width) for each port signal *)
  let of_signals signals =
    List.map signals ~f:(fun signal -> port_name signal, Signal.width signal)
  in

  ( of_signals (Circuit.inputs circuit) @ Circuit.phantom_inputs circuit
  , of_signals (Circuit.outputs circuit) )
;;

(* The Tiny Tapeout wrapper ports, as (name, width); the TT user module template has
   exactly these, so the design interface has to as well;
*)
let expected_inputs = [ "ui_in", 8; "uio_in", 8; "ena", 1; "clk", 1; "rst_n", 1 ]
let expected_outputs = [ "uo_out", 8; "uio_out", 8; "uio_oe", 8 ]

(* Check the circuit's top level is exactly the TT wrapper interface; returns Or_error
   rather than raising since [resolve] folds it into one combined TT error;

   Per direction, for every expected port:
   wrong width     : the port exists in this direction with another width;
   wrong direction : the port is missing here but exists in the other direction;
   missing         : the port exists in neither direction;
   and then:
   unexpected      : every port in this direction that is not expected, reported together;

   Both directions are checked and every problem is reported in one combined error.

   Careful: a port in the wrong direction is reported twice. The Wrong_direction fixture
   declares "rst_n" as an output, so the input check reports it as the wrong direction
   AND the output check reports it as unexpected. That is noise, not a missed error.

   TODO (future fix): these messages do not follow the structured diagnostic convention,
   which wants "what went wrong; how to fix it". Something like "TT top-level port has
   the wrong width; match the TT user module template". Changing them changes the
   fragments test/test_resolved_flow.ml looks for, so update those in the same commit;
*)
let validate_interface circuit =

  let inputs, outputs = ports circuit in

  (* Every error for one direction; [actual] is this direction's ports, [other] the
     opposite direction's, used only to tell a wrong direction from a missing port *)
  let check ~direction ~expected ~actual ~other =

    (* One error per expected port that is not present with the right width *)
    let errors =
      List.filter_map expected ~f:(fun (name, width) ->
        match List.Assoc.find actual name ~equal:String.equal with

        (* Present with the right width; nothing to report *)
        | Some actual_width when actual_width = width -> None

        (* Present in the right direction, but with another width *)
        | Some actual_width ->
          Some
            (Or_error.error_s
               [%message
                 "TT top-level port has the wrong width"
                   (name : string)
                   (direction : string)
                   (width : int)
                   (actual_width : int)])

        (* Not in this direction; either it is in the other one, or nowhere at all *)
        | None ->
          if List.Assoc.mem other name ~equal:String.equal
          then
            Some
              (Or_error.error_s
                 [%message
                   "TT top-level port has the wrong direction"
                     (name : string)
                     (direction : string)])
          else
            Some
              (Or_error.error_s
                 [%message
                   "TT top-level port is missing" (name : string) (direction : string)]))
    in

    (* Ports in this direction the wrapper does not have *)
    let unexpected =
      List.filter actual ~f:(fun (name, _) ->
        not (List.Assoc.mem expected name ~equal:String.equal))
    in

    errors
    @
    if List.is_empty unexpected
    then []
    else
      [ Or_error.error_s
          [%message
            "unexpected TT top-level ports"
              (direction : string)
              (unexpected : (string * int) list)]
      ]
  in

  Or_error.combine_errors_unit
    (check ~direction:"input" ~expected:expected_inputs ~actual:inputs ~other:outputs
     @ check ~direction:"output" ~expected:expected_outputs ~actual:outputs ~other:inputs
    )
;;

(* A span as a float number of nanoseconds; through seconds, so fractional nanoseconds
   survive (a 48 MHz period is 20.833... ns);
*)
let span_ns span = Time_float.Span.to_sec span *. 1e9

(* Resolve the declared clock and I/O delays against the elaborated ports; returns the
   clock, its period in nanoseconds and both delay lists as Delay_ns.t, sorted by port;
   returns Or_error rather than raising since [resolve] binds it after its other checks;

   1. exactly one clock, on "clk" -> otherwise error, and no delay is checked at all;
   2. per delay list, every delay whose maximum is not finite or is negative -> error;
   3. otherwise, every delay whose minimum is not finite or exceeds its maximum -> error;
   4. otherwise, every delay on "clk" or on a port not in that direction -> error;
   5. a port delayed more than once in the same list -> error; only the first repeat
      found is reported;
   6. no errors -> sort by port and convert both bounds to nanoseconds;

   Steps 2 to 5 run for both lists, and every problem found is reported in one combined
   error. Steps 2 to 4 are one if/else chain, so a delay with a bad bound and a bad port
   only reports the bound.

   A negative minimum is accepted on purpose: it is how SDC models an output feeding a
   receiver with a hold time, or an input that may change before the clock edge.

   NOT checked: a maximum longer than the clock period, and a delay on a phantom input
   (those are real wrapper ports, see [ports]); both are accepted as declared.
*)
let validate_timing elaborated_project =

  let circuit = Elaborated_project.circuit elaborated_project in
  let inputs, outputs = ports circuit in

  (* The one primary clock; TT wires its clock to "clk" and the SDC assumes it *)
  let%bind.Or_error clock =
    match Elaborated_project.clocks elaborated_project with
    | [ clock ] when String.equal clock.port "clk" -> Ok clock
    | clocks ->
      Or_error.error_s
        [%message
          "TT/LibreLane requires exactly one primary clock on clk"
            (clocks : Platform.Clock.t list)]
  in

  let period_ns = span_ns clock.period in
  let timing = Elaborated_project.timing elaborated_project in

  (* Steps 2 to 6 for one direction; [actual] is that direction's (name, width) ports *)
  let check_delays direction actual delays =

    (* Steps 2 to 4; one error per bad delay *)
    let errors =
      List.filter_map delays ~f:(fun (delay : Platform.Timing.Delay.t) ->
        let minimum_ns = span_ns delay.minimum in
        let maximum_ns = span_ns delay.maximum in
        if (not (Float.is_finite maximum_ns)) || Float.(maximum_ns < 0.)
        then
          Some
            (Or_error.error_s
               [%message
                 "timing delay must be finite and nonnegative"
                   (direction : string)
                   (delay : Platform.Timing.Delay.t)])
        else if (not (Float.is_finite minimum_ns)) || Float.(minimum_ns > maximum_ns)
        then
          Some
            (Or_error.error_s
               [%message
                 "timing delay minimum must be finite and no greater than its maximum"
                   (direction : string)
                   (delay : Platform.Timing.Delay.t)])
        else if String.equal delay.port "clk"
                || not (List.Assoc.mem actual delay.port ~equal:String.equal)
        then
          Some
            (Or_error.error_s
               [%message
                 "timing delay must name a nonclock top-level port in the stated \
                  direction"
                   (direction : string)
                   (delay : Platform.Timing.Delay.t)])
        else None)
    in

    (* Step 5; compares [port] only, so the same port with two bounds is a duplicate *)
    let duplicate =
      List.find_a_dup delays ~compare:(fun a b -> String.compare a.port b.port)
    in

    (* The duplicate, if any, goes in front of the other errors *)
    let errors =
      match duplicate with
      | None -> errors
      | Some duplicate ->
        Or_error.error_s
          [%message
            "timing delay port declared more than once"
              (direction : string)
              (duplicate : Platform.Timing.Delay.t)]
        :: errors
    in

    (* Step 6 *)
    let%map.Or_error () = Or_error.combine_errors_unit errors in
    List.sort delays ~compare:(fun a b -> String.compare a.port b.port)
    |> List.map ~f:(fun (delay : Platform.Timing.Delay.t) ->
      { Delay_ns.port = delay.port
      ; minimum       = span_ns delay.minimum
      ; maximum       = span_ns delay.maximum
      })
  in

  (* let%map ... and combines the errors of both lists, rather than stopping at inputs *)
  let%map.Or_error input_delays_ns = check_delays "input" inputs timing.input_delays
  and output_delays_ns = check_delays "output" outputs timing.output_delays in
  clock, period_ns, input_delays_ns, output_delays_ns
;;

(* The technology view roles a LibreLane [operation] cannot run without;

   Synthesis : PDK configuration, standard cell Liberty and Verilog;
   Hardening : all of the above, plus technology LEF and standard cell LEF and GDS;

   The list order is the order [validate_capabilities] reports missing roles in.

   An exhaustive match with no wildcard on purpose: a new Flow.Operation constructor fails
   to compile here until someone decides which views it needs.
*)
let required_views operation =
  let open Resource.Technology.Cmos5l.View.Role in
  match operation with
  | Flow.Config.Operation.Synthesis ->
    [ Pdk_configuration; Standard_cell_liberty; Standard_cell_verilog ]
  | Hardening ->
    [ Pdk_configuration
    ; Technology_lef
    ; Standard_cell_lef
    ; Standard_cell_gds
    ; Standard_cell_verilog
    ; Standard_cell_liberty
    ]
;;

(* Check the elaborated project and resolved target can run the selected flow; returns
   Or_error rather than raising since [resolve] folds it into one combined TT error;

   mode    : the project must come from Implementation elaboration; Simulation
             carries behavioral models, not synthesis RTL;
   views   : every role [required_views] names for the flow's operation has a
             Technology_view in [target.views]; a Floorplan_def never counts;
    macros  : only the stock exact SRAM is admitted; resolve_macros checks its
              complete source inventory and actual emitted instance name;
   sources : every resource carries Generated_synthesis_rtl; skipped for a Simulation
             elaboration, since the mode check already fails and would be followed by
             every resource again;

   All four checks run even when another fails, so one call reports every problem.

    Unknown macros retain both capability/source diagnostics. The stock macro's
    generated wrapper and supplied black box are checked separately below.

   Taking the target separately rather than resolving it here lets a test hand it a
   target with a view removed -> see expect tests (test/test_resolved_flow.ml);
*)
let validate_capabilities elaborated_project target =

  let flow = Elaborated_project.flow elaborated_project in
  let operation = flow.operation in

  (* Required roles with no matching technology view in the target *)
  let missing =
    List.filter (required_views flow.operation) ~f:(fun role ->
      not
        (List.exists target.Platform.Target.Resolved.views ~f:(fun view ->
           match view.role with
           | Technology_view actual -> Resource.Technology.Cmos5l.View.Role.equal actual role
           | Floorplan_def -> false)))
  in

  let resources = Elaborated_project.resources elaborated_project in

  (* Resources selected as a hard macro *)
  let unsupported_macros =
    List.filter resources ~f:(fun resource ->
      match resource.selection.implementation with
      | Resource.Selection.Implementation.Macro name -> not (String.equal name stock_macro)
      | Flops -> false)
  in

  (* Resources with no synthesis RTL; empty for Simulation elaboration, see above *)
  let missing_synthesis_sources =
    if Mode.equal
         (Elaborated_project.mode elaborated_project) Mode.Simulation
    then []
    else
      List.filter resources ~f:(fun resource ->
         (match resource.selection.implementation with
          | Resource.Selection.Implementation.Macro name when String.equal name stock_macro -> false
          | _ -> not (List.mem
             resource.sources
             Resource.Record.Source.Generated_synthesis_rtl
              ~equal:Resource.Record.Source.equal)))
  in

  (* Checking with the above in a list format, fold any errors into the result; *)
  Or_error.combine_errors_unit
    [ (if Mode.equal
            (Elaborated_project.mode elaborated_project) Mode.Implementation
       then Ok ()
       else
         Or_error.error_s
           [%message "flow resolution requires implementation elaboration"])

    ; (if List.is_empty missing
       then Ok ()
       else
         Or_error.error_s
           [%message
             "LibreLane operation lacks required technology views"
               (operation : Flow.Config.Operation.t)
               (missing : Resource.Technology.Cmos5l.View.Role.t list)])

    ; (if List.is_empty unsupported_macros
       then Ok ()
       else
         Or_error.error_s
           [%message
             "TT/LibreLane macro collateral is not supported by this reference adapter"
               (unsupported_macros : Resource.Record.t list)])

    ; (if List.is_empty missing_synthesis_sources
       then Ok ()
       else
         Or_error.error_s
           [%message
             "implementation resources need generated synthesis RTL"
               (missing_synthesis_sources : Resource.Record.t list)])
    ]
;;

(* LibreLane keys an override may never set, because the value is a fact owned by the
   declaration rather than a tuning knob;

   design   : DESIGN_NAME;
   sources  : VERILOG_FILES, generated by Bundle.config;
   target   : DIE_AREA, FP_DEF_TEMPLATE, the power pins, PDK, STD_CELL_LIBRARY, every
              routing layer bound and FP_IO_*LAYER, FP_PIN_ORDER_CFG; fixed by the TT
              floorplan template and PDK, even where no setting is derived for them;
   clock    : CLOCK_PORT, CLOCK_PERIOD;
   resource : MACROS, EXTRA_LEFS, EXTRA_GDS_FILES, EXTRA_LIBS, EXTRA_VERILOG_MODELS,
              SYNTH_BLACKBOXES; macro collateral, which is not supported yet;
   timing   : SYNTH_DRIVING_CELL, SYNTH_CLK_DRIVING_CELL, OUTPUT_CAP_LOAD,
              MAX_FANOUT_CONSTRAINT, MAX_TRANSITION_CONSTRAINT,
              MAX_CAPACITANCE_CONSTRAINT, CLOCK_UNCERTAINTY_CONSTRAINT,
              CLOCK_TRANSITION_CONSTRAINT, TIME_DERATING_CONSTRAINT,
              IO_DELAY_CONSTRAINT; the constraint environment, owned by the resolved
              target and written into the SDC by Bundle.sdc;

   Anything containing "SDC" is protected too, see [protected_key].

   WHY the timing keys are protected rather than merely unused: with PNR_SDC_FILE set,
   LibreLane's base.sdc never runs, so the four purely analytical ones
   (CLOCK_UNCERTAINTY_CONSTRAINT, CLOCK_TRANSITION_CONSTRAINT, TIME_DERATING_CONSTRAINT,
   IO_DELAY_CONSTRAINT) reach nothing at all and an override of one would silently do
   nothing. The others are worse: synthesis reads SYNTH_DRIVING_CELL and OUTPUT_CAP_LOAD
   straight out of the configuration (it writes its own small ABC SDC from them), and ABC
   reads MAX_FANOUT_CONSTRAINT and MAX_TRANSITION_CONSTRAINT, so an override would change
   what synthesis optimises for while [Bundle.sdc] still analysed the target's values.
   A project that needs different values passes them through Target.Inputs, where they are
   checked and recorded as Target owned -> see Technology.Cmos5l.Constraints.

   Careful: this list is separate from what [resolve_settings] derives. A new derived
   setting whose key is not added here can be silently replaced by an override; that is
   only intended for the Default owned ones (FP_SIZING, RUN_LINTER).
*)
let protected_keys =
  [ "DESIGN_NAME"
  ; "VERILOG_FILES"
  ; "DIE_AREA"
  ; "FP_DEF_TEMPLATE"
  ; "VDD_PIN"
  ; "GND_PIN"
  ; "RT_MAX_LAYER"
  ; "RT_MIN_LAYER"
  ; "GRT_MIN_LAYER"
  ; "GRT_MAX_LAYER"
  ; "DRT_MIN_LAYER"
  ; "DRT_MAX_LAYER"
  ; "FP_IO_HLAYER"
  ; "FP_IO_VLAYER"
  ; "CLOCK_PORT"
  ; "CLOCK_PERIOD"
  ; "PDK"
  ; "STD_CELL_LIBRARY"
  ; "MACROS"
  ; "EXTRA_LEFS"
  ; "EXTRA_GDS_FILES"
  ; "EXTRA_LIBS"
  ; "EXTRA_VERILOG_MODELS"
  ; "EXTRA_SPICE_MODELS"
  ; "EXTRA_CDLS"
  ; "SYNTH_BLACKBOXES"
  ; "VERILOG_FILES_BLACKBOX"
  ; "PDN_MACRO_CONNECTIONS"
  ; "PDN_CFG"
  ; "STA_CORNERS"
  ; "DEFAULT_CORNER"
  ; "FP_PIN_ORDER_CFG"
  ; "SYNTH_DRIVING_CELL"
  ; "SYNTH_CLK_DRIVING_CELL"
  ; "OUTPUT_CAP_LOAD"
  ; "MAX_FANOUT_CONSTRAINT"
  ; "MAX_TRANSITION_CONSTRAINT"
  ; "MAX_CAPACITANCE_CONSTRAINT"
  ; "CLOCK_UNCERTAINTY_CONSTRAINT"
  ; "CLOCK_TRANSITION_CONSTRAINT"
  ; "TIME_DERATING_CONSTRAINT"
  ; "IO_DELAY_CONSTRAINT"
  ]
;;

(* Whether an override may NOT set [key]; any key in [protected_keys], or any key
   containing "SDC";

   The substring match is broad on purpose: timing constraints only come from Timing and
   Clock through Bundle.sdc, and Bundle.config generates PNR_SDC_FILE and
   SIGNOFF_SDC_FILE, so every SDC related key ("PNR_SDC_FILE", "SIGNOFF_SDC_FILE", ...)
   is rejected without listing each one;
*)
let protected_key key =
  List.mem protected_keys key ~equal:String.equal
  || String.is_substring key ~substring:"SDC"
;;

(* Derive the LibreLane settings and merge the overrides over them; returns Or_error
   rather than raising since [resolve] binds it as its last check;

   1. derive a setting per design, target and clock fact, plus two Default ones;
   2. pull the overrides out of the flow adapter;
   3. any override on a protected key -> error, every conflict reported together;
   4. drop each derived setting an override replaces, then add the overrides, owned by
      Override with their reason;
   5. sort by key;

   Why one setting per key: derived keys are distinct by construction, Flow.validate
   already rejected overrides that repeat a key, and step 4 drops every derived setting
   an override shares a key with. After step 3 only FP_SIZING and RUN_LINTER can be
   dropped that way.

   Careful: DIE_AREA prints each bound with "%.12g", so a coordinate with more than 12
   significant digits is rounded in the config; real TT die areas are far from that.

   Six of the Target settings restate the constraint environment Bundle.sdc renders. They
   are derived rather than left out because LibreLane reads three of them outside the SDC
   (SYNTH_DRIVING_CELL and OUTPUT_CAP_LOAD in synthesis, MAX_FANOUT_CONSTRAINT in ABC), so
   omitting them would let synthesis optimise against the PDK's values while analysis used
   the target's. The other three reach nothing while PNR_SDC_FILE is set; they are written
   anyway so config.json states the same environment the SDC applies, and so they still
   agree if a later LibreLane reads them. Every one of the ten keys involved is protected
   -> see [protected_keys].
*)
let resolve_settings elaborated_project target clock_period_ns =

  let mk key value owner : Setting.t = { key; value; owner } in
  let rect = target.Platform.Target.Resolved.die_area_um in

  (* The target's constraint environment; the same record Bundle.sdc renders, so the six
     settings derived from it below state exactly what the SDC applies *)
  let environment = target.constraints in

  (* Step 1; CLOCK_PORT can be "clk" outright since [validate_timing] already required
     it; FP_DEF_TEMPLATE is still support-tools relative, Bundle.config rewrites it *)
  (* TODO (possible fix): PDK repeats [target.technology_name] and STD_CELL_LIBRARY is a
     literal too; fine while only ihp-sg13cmos5l resolves, but both should come from the
     resolved target once a second technology does; *)
  let derived =
    [ mk "DESIGN_NAME"
        (`String (Circuit.name (Elaborated_project.circuit elaborated_project))) Design
    ; mk
        "DIE_AREA"
        (`String
          (String.concat
             ~sep:" "
             (List.map
                [ rect.x_min; rect.y_min; rect.x_max; rect.y_max ]
                ~f:(Printf.sprintf "%.12g"))))
        Target
    ; mk "FP_DEF_TEMPLATE" (`String target.floorplan.path) Target
    ; mk "VDD_PIN" (`String (fst target.harness_power_pins)) Target
    ; mk "GND_PIN" (`String (snd target.harness_power_pins)) Target
    ; mk "RT_MAX_LAYER" (`String target.top_routing_layer) Target
    ; mk "PDK" (`String "ihp-sg13cmos5l") Target
    ; mk "STD_CELL_LIBRARY" (`String "sg13cmos5l_stdcell") Target
    ; mk "CLOCK_PORT" (`String "clk") Clock
    ; mk "CLOCK_PERIOD" (`Float clock_period_ns) Clock
    ; mk
        "SYNTH_DRIVING_CELL"
        (`String (Resource.Technology.Cmos5l.Constraints.driving_cell_setting environment))
        Target
    ; mk "OUTPUT_CAP_LOAD" (`Float environment.output_cap_load_ff) Target
    ; mk "MAX_FANOUT_CONSTRAINT" (`Int environment.max_fanout) Target
    ; mk "CLOCK_UNCERTAINTY_CONSTRAINT" (`Float environment.clock_uncertainty_ns) Target
    ; mk "CLOCK_TRANSITION_CONSTRAINT" (`Float environment.clock_transition_ns) Target
    ; mk "TIME_DERATING_CONSTRAINT" (`Float environment.time_derating_percent) Target
    ; mk "FP_SIZING" (`String "absolute") Default
    ; mk "RUN_LINTER" (`Int 1) Default
    ]
  in

  (* Step 2; no wildcard, so a new Flow.Adapter fails to compile here *)
  let overrides =
    match (Elaborated_project.flow elaborated_project).adapter with
    | Flow.Config.Adapter.Librelane settings -> settings.overrides
  in

  (* Step 3 *)
  let conflicts = List.filter overrides ~f:(fun o -> protected_key o.key) in
  let%map.Or_error () =
    if List.is_empty conflicts
    then Ok ()
    else
      Or_error.error_s
        [%message
          "LibreLane override conflicts with a generated design, target, clock, source, \
           or resource fact"
            (conflicts : Flow.Config.Librelane.Override.t list)]
  in

  (* Step 4 *)
  let entries =
    List.filter derived ~f:(fun entry ->
      not (List.exists overrides ~f:(fun o -> String.equal o.key entry.key)))
    @ List.map overrides ~f:(fun o -> mk o.key o.value (Override o.reason))
  in

  (* Step 5 *)
  List.sort entries ~compare:(fun a b -> String.compare a.key b.key)
;;

(* The main sauce here for flow resolution; given an Implementation elaboration, check it
   against the TT/LibreLane flow and return everything Bundle needs;

   1. resolve the target -> an unsupported harness and technology pair stops here, with
      nothing else checked;
   2. check the interface, metadata, pinout and capabilities, every problem reported in
      one combined error;
   3. [validate_timing] -> resolved clock and delays;
   4. [resolve_settings] -> merged settings;
   5. assemble, deriving [clock_hz] from the period;

   Steps are bound in order, so a failure in one hides the ones after it; a protected
   override is only reported once the interface and timing are clean.

   Metadata author and description are checked here, not in Metadata.validate, which
   only requires a title; a TT submission needs both, a project that only simulates
   does not.

   Returns Or_error rather than raising since it runs after elaboration, outside any
   design constructor; Project.elaborate_for_flow tags the error with the project name.
   [inputs] passes straight through to Target.resolve, which defaults it to
   Target.Inputs.reference_cmos5l_6x4 -> see expect tests (test/test_resolved_flow.ml);
*)
(* The catalog is already checked. Merge its complete contribution history over
   checked project values, without inventing observed backend defaults. *)
let merge_methodology methodology settings =
  let open Or_error.Let_syntax in
  let module P = Hardcaml_asic_flow_domain.Parameter in
  let configurable =
    Hardcaml_asic_flow_domain.Catalog.entry methodology |> Hardcaml_asic_flow_domain.Catalog.Entry.methodology
    |> Hardcaml_asic_flow_domain.Methodology.configurable
  in
  let%bind inherited =
    List.filter_map settings ~f:(fun (s : Setting.t) ->
      match P.Key.of_string s.key with
      | Error _ -> None
      | Ok key when not (List.mem configurable key ~equal:P.Key.equal) -> None
      | Ok _ ->
        Some
          (let%map assignment =
             match s.value with
             | `String v -> P.of_string_value ~key:s.key v
             | `Bool v -> P.of_bool_value ~key:s.key v
             | _ -> Or_error.errorf "invalid project methodology value for %s" s.key
           in
           { Effective_parameter.assignment; owner = s.owner }))
    |> Or_error.combine_errors
  in
  let key c = P.assignment_key c.Effective_parameter.assignment in
  let from_catalog c =
    { Effective_parameter.assignment = P.assignment c
    ; owner = Setting.Owner.Methodology (P.origin c)
    }
  in
  let parameters = Hardcaml_asic_flow_domain.Catalog.parameters methodology in
  let effective =
    List.map parameters ~f:(fun p ->
      let winning = from_catalog (P.winning p) in
      let project = List.filter inherited ~f:(fun c -> P.Key.equal (key c) (key winning)) in
      { Effective_parameter.winning
      ; overridden = project @ List.map (P.overridden p) ~f:from_catalog
      })
    @ List.filter_map inherited ~f:(fun winning ->
      if List.exists parameters ~f:(fun p ->
        P.Key.equal (P.assignment_key (P.assignment (P.winning p))) (key winning))
      then None
      else Some { Effective_parameter.winning; overridden = [] })
    |> List.sort ~compare:(fun a b ->
      String.compare (P.Key.to_string (key a.winning)) (P.Key.to_string (key b.winning)))
  in
  let%map () =
    Or_error.combine_errors_unit
      (List.map effective ~f:(fun p ->
         let k = P.Key.to_string (key p.winning) in
         if protected_key k then Or_error.errorf "methodology conflicts with protected key %s" k
         else Ok ()))
  in
  let typed_settings =
    List.map effective ~f:(fun p ->
      let c = p.winning in
      let value =
        match P.assignment_value c.assignment with
        | Strategy s -> `String (P.Strategy.to_string s)
        | Bool b -> `Bool b
      in
      { Setting.key = P.Key.to_string (key c); value; owner = c.owner })
  in
  let settings =
    List.filter settings ~f:(fun s ->
      not (List.exists typed_settings ~f:(fun t -> String.equal s.Setting.key t.key)))
    @ typed_settings
    |> List.sort ~compare:(fun a b -> String.compare a.Setting.key b.key)
  in
  settings, effective
;;

let resolve_effective_parameters ~methodology ~project_assignments =
  let open Or_error.Let_syntax in
  let module P = Hardcaml_asic_flow_domain.Parameter in
  let configurable = Hardcaml_asic_flow_domain.Methodology.configurable
    (Hardcaml_asic_flow_domain.Catalog.Entry.methodology (Hardcaml_asic_flow_domain.Catalog.entry methodology)) in
  let%bind _ = P.check_assignments ~configurable (List.map project_assignments ~f:fst) in
  let overrides = List.map project_assignments ~f:(fun (a, reason) ->
    let value = match P.assignment_value a with
      | Strategy s -> `String (P.Strategy.to_string s) | Bool b -> `Bool b in
    { Flow.Config.Librelane.Override.key = P.Key.to_string (P.assignment_key a); value; reason }) in
  let%bind () = Flow.Config.Librelane.validate { overrides; macros = [] } in
  let settings = List.map overrides ~f:(fun o ->
    { Setting.key = o.key; value = o.value; owner = Override o.reason }) in
  let%map _, effective = merge_methodology methodology settings in
  effective
;;

(* Pure checks against the actual Hardcaml-mangled RTL name. Never read a PDK. *)
let resolve_macros elaborated_project target =
  let open Or_error.Let_syntax in
  let resources = Elaborated_project.resources elaborated_project in
  let flow = Elaborated_project.flow elaborated_project in
  let intents = match flow.adapter with Librelane s -> s.macros in
  let circuit = Elaborated_project.circuit elaborated_project in
  let%bind () = Or_error.combine_errors_unit (List.map intents ~f:(fun intent ->
    match List.find resources ~f:(fun r -> Resource.Id.equal r.id intent.resource) with
    | Some { selection = { implementation = Macro _; _ }; _ } -> Ok ()
    | _ -> Or_error.error_s [%message "macro intent must reference a selected macro resource" (intent : Flow.Macro_intent.t)])) in
  let%bind macros = List.filter_map resources ~f:(fun resource ->
    match resource.selection.implementation with
    | Flops -> None
    | Macro name -> Some (
      let%bind () = if String.equal name stock_macro then Ok ()
        else Or_error.errorf "unsupported macro bundle implementation: %s" name in
      let%bind () = if Resource.Request.equal resource.request (Resource.Request.single_port_ram ~width:16 ~depth:256 ~read_latency:1)
        then Ok () else Or_error.error_string "stock SRAM requires the exact 256x16 latency-one request" in
      let instance = Resource.Id.rtl_instance_name resource.id in
      let actual = Circuit.instantiations circuit |> List.filter ~f:(fun inst ->
        String.equal inst.instantiation.circuit_name name
        && String.equal (Circuit.Name_for_rtl.instantiation_exn circuit inst
          |> Rtl.Name.legalize ~language:Verilog |> Rtl.Name.For_backend.to_string) instance) in
      let%bind () = if List.length actual = 1 then Ok () else
        Or_error.error_s [%message "selected macro must have exactly one matching actual emitted RTL instance" (instance : string) (name : string)] in
      let collateral = List.filter_map resource.sources ~f:(function
        | Resource.Record.Source.Collateral c -> Some c | _ -> None) in
      let open Resource.Technology.Collateral in
      let required =
        [ Role.Black_box, "generated:" ^ stock_macro ^ ".blackbox.v"
        ; Role.Physical_abstract, macro_path "lef" ".lef"
        ; Role.Physical_layout, macro_path "gds" ".gds"
        ; Role.Spice_netlist, macro_path "cdl" ".cdl"
        ] @ List.map macro_corners ~f:(fun (_, suffix) ->
          let corner = List.hd_exn (String.split suffix ~on:'_') in
          Role.Timing_model { corner }, macro_path "lib" ("_" ^ suffix ^ ".lib")) in
      let%bind () = Or_error.combine_errors_unit (List.map required ~f:(fun (role, reference) ->
        match List.filter collateral ~f:(fun c -> Role.equal c.role role) with
        | [c] when String.equal c.reference reference -> Ok ()
        | _ -> Or_error.error_s [%message "missing, ambiguous or unsupported exact SRAM collateral" (role : Role.t) (reference : string)])) in
      let%bind () = Or_error.combine_errors_unit (List.map [Role.Simulation_model; Role.Timed_simulation_model] ~f:(fun role ->
        let actual = List.filter_map collateral ~f:(fun c -> if Role.equal c.role role then Some c.reference else None)
          |> List.sort ~compare:String.compare in
        let expected = [macro_path "verilog" ".v";
          "pdk_dir::libs.ref/sg13cmos5l_sram/verilog/RM_IHPSG13_1P_core_behavioral_bm_bist.v"]
          |> List.sort ~compare:String.compare in
        if List.equal String.equal actual expected then Ok () else
          Or_error.error_s [%message "exact SRAM requires both vendor simulation sources" (role : Role.t)])) in
      let%bind () = if List.length collateral = 11 then Ok () else
        Or_error.error_string "unexpected exact SRAM collateral roles" in
      let%bind () = if List.for_all collateral ~f:(fun c ->
        String.is_prefix c.reference ~prefix:"pdk_dir::libs.ref/"
        || String.equal c.reference ("generated:" ^ stock_macro ^ ".blackbox.v"))
        then Ok () else Or_error.error_string "macro collateral must use supported PDK-relative references" in
      let intent = List.find intents ~f:(fun i -> Resource.Id.equal i.resource resource.id) in
      let%map () = match intent with
        | None -> Ok ()
        | Some i ->
          let r = target.Platform.Target.Resolved.die_area_um in
          if Float.(i.x_um -. i.halo_um >= r.x_min && i.y_um -. i.halo_um >= r.y_min
                    && i.x_um +. 236.8 +. i.halo_um <= r.x_max
                    && i.y_um +. 118.78 +. i.halo_um <= r.y_max)
          then Ok () else Or_error.error_string "macro placement and halo exceed target bounds" in
      { Macro.resource = resource.id; name; instance; collateral; intent }))
    |> Or_error.combine_errors in
  let%bind () = if List.length (List.filter (Circuit.instantiations circuit) ~f:(fun i ->
    String.equal i.instantiation.circuit_name stock_macro)) = List.length macros
    then Ok () else Or_error.error_string "unregistered stock SRAM RTL instance" in
  let%bind () = Or_error.combine_errors_unit (List.concat_mapi macros ~f:(fun n a ->
    List.filter_mapi macros ~f:(fun m b -> if m <= n then None else
      match a.intent, b.intent with
      | Some a, Some b -> Some (
        let gap = a.halo_um +. b.halo_um in
        if Float.(a.x_um +. 236.8 +. gap <= b.x_um || b.x_um +. 236.8 +. gap <= a.x_um
                  || a.y_um +. 118.78 +. gap <= b.y_um || b.y_um +. 118.78 +. gap <= a.y_um)
        then Ok () else Or_error.error_string "macro placements or halos overlap")
      | _ -> None))) in
  Ok macros
;;

let resolve ?inputs ?methodology elaborated_project =

  let%bind.Or_error target =
    Platform.Target.resolve ?inputs (Elaborated_project.target elaborated_project)
  in
  let metadata = Elaborated_project.metadata elaborated_project in

  (* Step 2 *)
  let%bind.Or_error () =
    Or_error.combine_errors_unit
      [ validate_interface (Elaborated_project.circuit elaborated_project)

      ; (if String.is_empty (String.strip metadata.author)
            || String.is_empty (String.strip metadata.description)
         then
           Or_error.error_s
             [%message "TT metadata needs a nonblank author and description"]
         else Ok ())

      ; Platform.Pinout.validate_tiny_tapeout (Elaborated_project.pinout elaborated_project)

      ; validate_capabilities elaborated_project target
      ]
  in

  let%bind.Or_error macros = resolve_macros elaborated_project target in
  let%bind.Or_error physical_plan =
    if List.is_empty macros
       || Flow.Config.Operation.equal (Elaborated_project.flow elaborated_project).operation Synthesis
    then Ok None
    else (
      let%bind.Or_error entries =
        Or_error.all (List.map macros ~f:(fun m ->
          match m.intent with
          | None -> Or_error.error_s [%message "hardening requires complete macro placement intent" (m.instance : string)]
          | Some intent -> Ok { Macro_physical_plan.resource = m.resource; instance = m.instance; name = m.name; intent }))
      in
      let%map.Or_error plan = Macro_physical_plan.create entries in
      Some plan)
  in
  (* Step 3 *)
  let%bind.Or_error clock, clock_period_ns, input_delays_ns, output_delays_ns =
    validate_timing elaborated_project
  in
  let%bind.Or_error timing_intent = Timing_intent.resolve
    ~circuit:(Elaborated_project.circuit elaborated_project)
    ~timing:(Elaborated_project.timing elaborated_project)
    ~registrations:(Elaborated_project.timing_stages elaborated_project) in

  (* Steps 4 and 5 *)
  let%bind.Or_error settings = resolve_settings elaborated_project target clock_period_ns in
  let%bind.Or_error settings, effective_parameters =
    match methodology with
    | None -> Ok (settings, [])
    | Some methodology -> merge_methodology methodology settings
  in
  let%map.Or_error () =
    match physical_plan with
    | None -> Ok ()
    | Some _ ->
      let project_keys = match (Elaborated_project.flow elaborated_project).adapter with
        | Librelane s -> List.map s.overrides ~f:(fun o -> o.key) in
      let keys = project_keys @ List.map settings ~f:(fun (s : Setting.t) -> s.key) in
      let conflicts = List.filter keys ~f:Macro_physical_plan.owns_key
        |> List.dedup_and_sort ~compare:String.compare in
      if List.is_empty conflicts then Ok () else
        Or_error.error_s [%message "physical macro generated settings conflict with overrides; remove owned keys and aliases" (conflicts : string list)]
  in
  { elaborated_project
  ; target
  ; clock
  ; clock_period_ns
  ; clock_hz = 1e9 /. clock_period_ns
  ; input_delays_ns
  ; output_delays_ns
   ; settings
   ; methodology
    ; effective_parameters
     ; macros
     ; physical_plan
     ; timing_intent
  }
;;

[@@@ocamlformat "enable"]
