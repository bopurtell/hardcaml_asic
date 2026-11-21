(* University of Florida *)
(* Author: Bohdan Purtell *)
(* Module: "elaboration/bundle.ml" *)
(* Source implementation of the TT/LibreLane project bundle. A bundle is the answer to
   "what exactly would be handed to the flow, and where did every byte come from?";

   It is an in-memory list of files plus a manifest describing them. [render] builds one
   out of a Resolved.t (and optionally a Simulation Elaborated_project.t),
   reading the declared source inputs and git state under [source_root]; [write] then puts
   it on disk. examples/tt_bundle_example.ml is the caller today: it runs
   Project.elaborate_for_flow and Project.elaborate ~mode:Simulation, renders, writes, and
   prints the identity.

   Rendering and writing are split on purpose: everything that decides the bundle's
   contents happens in [render], without touching the output directory, so a bundle can be
   inspected through [file], [paths] and [manifest] before anything lands on disk, and a
   render failure (a missing input, no git commit) writes nothing.

   It does NOT validate the design, the target or the settings; Elaboration.Resolved
   already did, and this module leans on those guarantees (one clock on "clk", nonempty
   views, no override of a generated setting). It does NOT run any EDA tool, touch the PDK
   or link the TT support-tools checkout at OUTPUT/tt; running the flow is P4 work. Layout
   and manifest fields are described in docs/guides/bundle-emission.md.
*)

(* Aliased before [open! Core] on purpose; Core shadows Unix with an empty, deprecated
   module, so after the open [Unix.realpath] would not even exist;
*)
module U = Unix
open! Core
open! Hardcaml

[@@@ocamlformat "disable"]

(* The bundle itself;

   files    : (bundle relative path, contents), sorted by path, with "manifest.json"
              appended last;
   manifest : the manifest JSON, identity field included; same bytes as "manifest.json"
              apart from pretty printing;
   identity : SHA-256 of the manifest payload without the identity field;

   Transparent here but abstract in the mli, so the only way to get one is [render], which
   means [files], [manifest] and [identity] always agree.
*)
type t =
  { files    : (string * string) list
  ; manifest : Yojson.Safe.t
  ; identity : string
  }

(* Lowercase hex SHA-256 of [contents]; Cryptokit hands back the 32 raw digest bytes, so
   they get hex encoded here. Same form sha256sum and Python's hexdigest print, which is
   what test/check_bundle.py compares against;
*)
let sha256 contents =
  Cryptokit.hash_string (Cryptokit.Hash.sha256 ()) contents
  |> String.to_list
  |> List.map ~f:(fun c -> Printf.sprintf "%02x" (Char.to_int c))
  |> String.concat
;;

(* Pretty printed JSON plus a trailing newline; how every .json file is written; *)
let json value = Yojson.Safe.pretty_to_string value ^ "\n"

(* Absence is the historical contract, never an empty or upgraded snapshot. A
   present snapshot is checked even when a caller has recomputed the outer hash. *)
let verify_resource_snapshot manifest =
  let module J = Preparation_json in
  match List.Assoc.find (J.fields manifest) "resource_snapshot" ~equal:String.equal with
  | None -> ()
  | Some snapshot ->
    let checked = Resource_snapshot.of_json snapshot |> Or_error.ok_exn in
    J.expect (J.get snapshot "project") (J.get manifest "project");
    J.expect (J.get snapshot "top") (J.get manifest "top_module");
    J.expect (J.get snapshot "mode") (`String "implementation");
    J.expect (J.get snapshot "scope") (`String "resolved");
    let records = Resource_snapshot.records checked in
    let legacy = J.list (J.get manifest "resources") in
    let sexp_equal json expected =
      if not (Sexp.equal (Sexp.of_string (J.string json)) expected)
      then J.fail "snapshot/resource inventory mismatch"
    in
    if List.length records <> List.length legacy then J.fail "snapshot/resource count mismatch";
    List.iter2_exn records legacy ~f:(fun (r : Resource.Record.t) old ->
      J.exact old ["id"; "request"; "requirement"; "policy"; "selection"; "elaborated_as"; "source_roles"];
      J.expect (J.get old "id") (`String (Resource.Id.to_string r.id));
      List.iter
        [ "request", Resource.Request.sexp_of_t r.request
        ; "requirement", Resource.Policy.Requirement.sexp_of_t r.requirement
        ; "policy", Resource.Policy.Applied.sexp_of_t r.policy
        ; "selection", Resource.Selection.sexp_of_t r.selection
        ; "elaborated_as", Resource.Record.Elaborated_as.sexp_of_t r.elaborated_as ]
        ~f:(fun (key, expected) -> sexp_equal (J.get old key) expected);
      let sources = J.list (J.get old "source_roles") in
      if List.length sources <> List.length r.sources then J.fail "snapshot/source count mismatch";
      List.iter2_exn sources r.sources ~f:(fun source expected ->
        sexp_equal source (Resource.Record.Source.sexp_of_t expected)));
    let expected_macros =
      J.list (J.get snapshot "resources") |> List.concat_map ~f:(fun r ->
        let actual = J.get r "actual_instances" in
        if String.equal (J.string (J.get actual "status")) "rtl_verified"
        then List.map (J.list (J.get actual "instances")) ~f:(fun i ->
          J.string (J.get (J.get r "id") "display"),
          J.string (J.get i "module"), J.string (J.get i "instance"))
        else [])
    in
    let macro_entries =
      match List.Assoc.find (J.fields manifest) "macro_contract" ~equal:String.equal with
      | None -> []
      | Some contract -> J.list (J.get contract "macros")
    in
    let macros = List.map macro_entries ~f:(fun m ->
        J.string (J.get m "resource"), J.string (J.get m "module"), J.string (J.get m "instance"))
    in
    if not (Poly.equal expected_macros macros) then J.fail "snapshot/macro inventory mismatch";
    List.iter macro_entries ~f:(fun m ->
      let resource = List.find_exn (J.list (J.get snapshot "resources")) ~f:(fun r ->
        Poly.equal (J.get (J.get r "id") "display") (J.get m "resource")) in
      let record = List.find_exn records ~f:(fun r ->
        String.equal (Resource.Id.to_string r.id) (J.string (J.get m "resource"))) in
      let expected_collateral = List.filter_map record.sources ~f:(function
        | Collateral c -> Some (Resource.Technology.Collateral.Role.sexp_of_t c.role, c.reference)
        | _ -> None) in
      (* The role S-expression includes the timing corner. Compare inventories,
         preserving multiplicity but not declaration order. The logical sources
         have already been checked against both snapshot and legacy resources. *)
      let actual_collateral = J.list (J.get m "collateral") |> List.map ~f:(fun c ->
        J.exact c ["role"; "reference"];
        Sexp.of_string (J.string (J.get c "role")), J.string (J.get c "reference")) in
      if not (Poly.equal
        (List.sort expected_collateral ~compare:Poly.compare)
        (List.sort actual_collateral ~compare:Poly.compare))
      then J.fail "snapshot/macro collateral inventory mismatch";
      let intent = J.get resource "physical_intent" in
      let physical = J.get m "physical_intent" in
      match J.string (J.get intent "status") with
      | "unavailable" -> J.expect physical `Null
      | "resolved_intent" ->
        List.iter ["location_um", "location_um"; "halo_um", "halo_um";
                   "orientation", "orientation"; "supplies", "power"]
          ~f:(fun (snapshot_key, macro_key) ->
            if not (String.equal (J.bytes (J.get intent snapshot_key)) (J.bytes (J.get physical macro_key)))
            then J.fail "snapshot/macro physical intent mismatch")
      | _ -> J.fail "resolved macro requires resolved physical intent")
;;

let verify_manifest_identity manifest =
  Or_error.try_with (fun () ->
    Flow.Plan_json.validate manifest;
    match manifest with
    | `Assoc fields ->
      let identity = Flow.Plan_json.string (Flow.Plan_json.get manifest "identity") in
      let payload = `Assoc (List.filter fields ~f:(fun (k, _) -> not (String.equal k "identity"))) in
      if not (String.equal identity (sha256 (Yojson.Safe.to_string payload)))
      then invalid_arg "bundle manifest identity mismatch";
      verify_resource_snapshot manifest;
      Option.iter (List.Assoc.find fields "hardware_variant" ~equal:String.equal)
        ~f:(fun j -> ignore (Hardware_variant.of_yojson j |> Or_error.ok_exn : Hardware_variant.t))
    | _ -> invalid_arg "expected bundle manifest object")
;;

(* Shorthand for a JSON string, so manifest builders read as key/value tables; *)
let string value = `String value

(* Shorthand for a JSON object; field order is kept as given, and it is part of the
   identity (see [render]);
*)
let assoc fields = `Assoc fields

(* Shorthand for a JSON list built by mapping [f] over [values]; *)
let list f values = `List (List.map values ~f)

(* An OCaml value as a JSON string holding its human readable sexp; the manifest records
   requests, selections and roles the same way the expect tests print them;
*)
let sexp to_sexp value = string (Sexp.to_string_hum (to_sexp value))

(* Run "git -C [root] [args]" and return its stdout; Or_error since a missing git or a
   [root] outside any repository is an ordinary [render] failure, not a bug;

   Only trailing newlines are dropped, never leading whitespace: porcelain status lines
   start with a meaningful space (" M lib/elaboration/elaborated_project.ml" is an unstaged edit), and
   test/check_bundle.py checks for exactly that prefix.

   git's own stderr is not captured; it goes straight to the process stderr.

   TODO (future fix): the failure message formats [args] into the string instead of
   attaching them as [%message] fields, against docs/development/comment_guidelines.md section 9, and
   it drops the exit status; see [read_input] for the other messages in this file;
*)
let run_git root args =
  let argv = Array.of_list ("git" :: "-C" :: root :: args) in
  Or_error.try_with (fun () ->
    let channel = U.open_process_args_in "git" argv in

    (* All of stdout, read before closing so git never blocks on a full pipe *)
    let output =
      Stdlib.In_channel.input_all channel
      |> String.rstrip ~drop:(Char.equal '\n')
    in

    match U.close_process_in channel with

    (* git exited cleanly; its output is the answer *)
    | U.WEXITED 0 -> output

    (* Nonzero exit, or killed by a signal; the output is discarded *)
    | _ -> failwithf "git failed: %s" (String.concat ~sep:" " args) ())
;;

(* Is [path] a plain relative path that stays under whatever it is joined onto?;

   ""             -> invalid;
   "/etc/passwd"  -> invalid, absolute;
   "lib//x.ml"    -> invalid, empty component;
   "./lib/x.ml"   -> invalid, "." component;
   "../x.ml"      -> invalid, ".." component;
   "lib/x.ml"     -> valid;

   Rejecting "." as well as ".." on purpose: every valid path then has exactly one
   spelling, so "inputs/<path>" in the bundle and "path" in the manifest are canonical.
   This is purely textual; symlinks are handled by [read_input].
*)
let valid_path path =
  not (String.is_empty path)
  && not (String.is_prefix path ~prefix:"/")
  && List.for_all (String.split path ~on:'/') ~f:(fun component ->
    not (String.is_empty component)
    && not (String.equal component ".")
    && not (String.equal component ".."))
;;

(* Read one declared source input's exact bytes;

   1. [path] fails [valid_path] -> error, before touching the file system;
   2. the joined path is missing or is a directory -> error;
   3. the resolved file is not under the resolved [source_root] -> error; this catches a
      symlink inside the root that points outside it;
   4. otherwise read the bytes, dirty or untracked alike;

   Careful: step 3 compares against [real_root ^ "/"], so a [source_root] of "/" becomes
   "//" and rejects every input. Nobody bundles from the file system root, but the error
   ("escapes source root") would be misleading if they did.

   TODO (future fix): these messages, [run_git]'s and [write]'s format paths into the
   string rather than attaching [%message] fields (docs/development/comment_guidelines.md section 9).
   test/check_bundle.py matches "source input is missing" in stderr, so change that
   assertion in the same commit;
*)
let read_input ~source_root path =
  if not (valid_path path)
  then Or_error.error_s [%message "invalid source input path" (path : string)]
  else (
    let full = Filename.concat source_root path in
    Or_error.try_with (fun () ->

      (* Step 2; a dangling symlink counts as missing, since file_exists follows it *)
      if not (Stdlib.Sys.file_exists full) || Stdlib.Sys.is_directory full
      then failwithf "source input is missing: %s" full ();

      (* Step 3 *)
      let real_root = U.realpath source_root in
      let real_file = U.realpath full in
      if not (String.is_prefix real_file ~prefix:(real_root ^ "/"))
      then failwithf "source input escapes source root: %s" path ();

      (* Step 4 *)
      In_channel.read_all full))
;;

(* Render constraints/top.sdc; the clock, the target's constraint environment, and a max
   and a min line per declared I/O delay;

   The clock name and port are both hardcoded to "clk"; that is safe because
   Resolved.validate_timing only accepts exactly one clock, on "clk". Delays arrive
   already sorted by port from the same function, and the port lists below are sorted
   here, so the file is deterministic;

   Numbers print with "%.17g", enough digits to round trip any float exactly, so the SDC
   period is the same number as CLOCK_PERIOD in config.json (test/check_bundle.py checks
   this) rather than a rounded neighbour. A consequence: a value with no exact binary
   form prints all of it, so a 5% derate writes -early 0.94999999999999996, which is the
   number the flow would have used either way;

   Both delay bounds are always written. A "-max" line alone leaves hold unconstrained, so
   STA finds no hold paths and the resizer inserts no hold buffers (see Timing);

   WHY the environment lines are here at all: Bundle.config sets PNR_SDC_FILE and
   SIGNOFF_SDC_FILE to this file, which stops LibreLane's FALLBACK_SDC (base.sdc) from
   ever running, and that script is the only place LibreLane applies uncertainty,
   transition, derate, driving cell, load and fanout. Without them a path through an
   input buffer has no input slew, no variation margin and no uncertainty, and hold is
   analysed optimistically: the reference observable design closed hold at +0.01 ns
   without these lines and at -0.24 ns with them, which is the violation the post-CTS
   resizer is supposed to repair.

   Deliberately absent: set_propagated_clock. LibreLane's read_current_sdc
   (scripts/openroad/common/io.tcl) greps this file for "set_propagated_clock" and
   "unset_propagated_clock", and applies the right one per step when it finds neither:
   ideal clocks for pre-CTS analysis, propagated clocks after CTS. Writing either here
   would pin every step to one of them.

   Also absent: set_max_transition and set_max_capacitance. The pinned PDK leaves
   MAX_TRANSITION_CONSTRAINT and MAX_CAPACITANCE_CONSTRAINT unset, so base.sdc would not
   have written them either, and the Liberty's own limits apply.
*)
let sdc ~timing_physical_qualification (resolved : Resolved.t) =

  let number value = Printf.sprintf "%.17g" value in
  let constraints = resolved.target.constraints in

  (* The ports of one direction as a sorted "a b c" list for get_ports; taken from the
     wrapper interface Resolved.validate_interface has already matched the design
     against, so every name here is a real port *)
  let ports names = String.concat ~sep:" " (List.sort names ~compare:String.compare) in

  (* Every input but the clock; the clock gets its own driving cell line, as base.sdc
     does, since a technology may characterise a different cell for it *)
  let signal_inputs =
    List.filter_map Resolved.expected_inputs ~f:(fun (name, _) ->
      Option.some_if (not (String.equal name "clk")) name)
  in

  let outputs = List.map Resolved.expected_outputs ~f:fst in

  (* One "set_driving_cell" line for a get_ports list *)
  let driving_cell port_list =
    Printf.sprintf
      "set_driving_cell -lib_cell %s -pin %s [get_ports {%s}]\n"
      constraints.driving_cell
      constraints.driving_cell_pin
      port_list
  in

  (* The "-max" then "-min" "set_<direction>_delay" lines for one resolved delay *)
  let delay direction ({ port; minimum; maximum } : Resolved.Delay_ns.t) =
    let line bound value =
      Printf.sprintf
        "set_%s_delay %s %s -clock clk [get_ports {%s}]\n"
        direction
        bound
        (number value)
        port
    in
    line "-max" maximum ^ line "-min" minimum
  in

  (* The derate multipliers; a percentage either side of 1, the same arithmetic base.sdc
     does, and why [constraint_problems] keeps the percentage below 100 *)
  let derate = constraints.time_derating_percent /. 100. in

  String.concat
    [ Printf.sprintf
        "create_clock -name clk -period %s [get_ports {clk}]\n"
        (number resolved.clock_period_ns)
    ; Printf.sprintf
        "set_clock_uncertainty %s [get_clocks {clk}]\n"
        (number constraints.clock_uncertainty_ns)
    ; Printf.sprintf
        "set_clock_transition %s [get_clocks {clk}]\n"
        (number constraints.clock_transition_ns)
    ; Printf.sprintf "set_timing_derate -early %s\n" (number (1. -. derate))
    ; Printf.sprintf "set_timing_derate -late %s\n" (number (1. +. derate))
    ; Printf.sprintf "set_max_fanout %d [current_design]\n" constraints.max_fanout
    ; driving_cell (ports signal_inputs)
    ; driving_cell (ports [ "clk" ])
    ; Printf.sprintf
        "set_load %s [get_ports {%s}]\n"
        (number (Resource.Technology.Cmos5l.Constraints.output_cap_load_pf constraints))
        (ports outputs)
    ; List.map resolved.input_delays_ns ~f:(delay "input") |> String.concat
    ; List.map resolved.output_delays_ns ~f:(delay "output") |> String.concat
    ; Option.value_map resolved.timing_intent ~default:"" ~f:(Timing_intent.sdc ~physical:timing_physical_qualification)
    ]
;;

(* Render the Tiny Tapeout info.yaml; project metadata, pinout and the single source file;

   Built as text rather than through a YAML library. Every user supplied string goes
   through [yaml_string], a JSON string literal, which is also a valid YAML double quoted
   scalar, so quotes, colons and non-ASCII text cannot break the document;

   clock_hz prints with "%.12g" rather than "%.17g": it is derived as 1e9 /. period, so a
   48 MHz clock would otherwise show float noise instead of "48000000"
   (test/check_bundle.py checks for exactly that);

   Fixed on purpose: discord is always empty, language is always "Verilog", source_files
   lists only [source_name] (the synthesis RTL, never the simulation RTL), and
   yaml_version is 6, the version the pinned TT support tools read;
*)
let info_yaml (resolved : Resolved.t) source_name =

  let elaborated_project = resolved.elaborated_project in
  let metadata = Elaborated_project.metadata elaborated_project in
  let yaml_string value = Yojson.Safe.to_string (`String value) in

  (* ui, then uo, then uio, the order TT's project_info.py reads them; no wildcard, so a
     new bank fails to compile here until someone places it *)
  let bank_order = function Platform.Pinout.Bank.Ui -> 0 | Uo -> 1 | Uio -> 2 in

  (* Pin lines sorted by bank, then bit, whatever order they were declared in *)
  let pins =
    List.sort (Elaborated_project.pinout elaborated_project) ~compare:(fun a b ->
      let c = Int.compare (bank_order a.bank) (bank_order b.bank) in
      if c <> 0 then c else Int.compare a.bit b.bit)
    |> List.map ~f:(fun pin ->
      Printf.sprintf
        "  %s[%d]: %s\n"
        (Platform.Pinout.Bank.to_string pin.bank)
        pin.bit
        (yaml_string pin.description))
    |> String.concat
  in

  String.concat
    [ "project:\n"
    ; "  title: " ^ yaml_string metadata.title ^ "\n"
    ; "  author: " ^ yaml_string metadata.author ^ "\n"
    ; "  discord: \"\"\n"
    ; "  description: " ^ yaml_string metadata.description ^ "\n"
    ; "  language: \"Verilog\"\n"
    ; "  clock_hz: " ^ Printf.sprintf "%.12g" resolved.clock_hz ^ "\n"
    ; "  tiles: " ^ yaml_string resolved.target.tiles ^ "\n"
    ; "  top_module: "
      ^ yaml_string (Circuit.name (Elaborated_project.circuit elaborated_project)) ^ "\n"
    ; "  source_files:\n    - " ^ yaml_string source_name ^ "\n"
    ; "\npinout:\n"
    ; pins
    ; "\nyaml_version: 6\n"
    ]
;;

(* Render the LibreLane configuration for src/config.json; the resolved settings plus the
   three settings only the bundle can know (where it put the RTL and the SDC);

   Every "dir::" path is relative to src/config.json itself:

   VERILOG_FILES   -> "dir::<top>.v", next to config.json;
   *_SDC_FILE      -> "dir::../constraints/top.sdc";
   FP_DEF_TEMPLATE -> rewritten from a support-tools relative path to "dir::../tt/<path>",
                      since the TT checkout is expected to be linked at OUTPUT/tt;

   Keys are sorted, so the JSON is deterministic whatever order Elaboration.Resolved
   produced. Why no duplicate keys: VERILOG_FILES and anything containing "SDC" are
   protected keys in Elaboration.Resolved, so no override can collide with a generated
   setting here;

   TODO (future fix): the three generated settings are written out again in [render]'s
   "generated_settings"; one shared list would stop the two from drifting apart;
*)
let macro_stub_path = "macros/" ^ Resolved.stock_macro ^ ".blackbox.v"

let macro_configuration (resolved : Resolved.t) =
  let names = List.dedup_and_sort (List.map resolved.macros ~f:(fun m -> m.name)) ~compare:String.compare in
  assoc (List.map names ~f:(fun name ->
    let macros = List.filter resolved.macros ~f:(fun m -> String.equal m.name name) in
    let paths role =
      List.hd_exn macros |> fun (m : Resolved.Macro.t) ->
      List.filter_map m.collateral ~f:(fun c ->
        if Resource.Technology.Collateral.Role.equal c.role role then Some (string c.reference) else None)
      |> fun xs -> `List xs in
    name, assoc
      [ "instances", assoc (List.filter_map macros ~f:(fun m ->
          Option.map m.intent ~f:(fun i -> m.instance, assoc
            [ "location", `List [`Float i.x_um; `Float i.y_um]
            ; "orientation", string "R0" ])))
      ; "lef", paths Physical_abstract
      ; "gds", paths Physical_layout
      ; "spice", paths Spice_netlist
      ; "lib", assoc (List.map Resolved.macro_corners ~f:(fun (corner, suffix) ->
          corner, `List [string (Resolved.macro_path "lib" ("_" ^ suffix ^ ".lib"))]))
      ]))
;;

let generated_settings (resolved : Resolved.t) source_name =
  [ "VERILOG_FILES", `List [ `String ("dir::" ^ source_name) ]
  ; "PNR_SDC_FILE", `String "dir::../constraints/top.sdc"
  ; "SIGNOFF_SDC_FILE", `String "dir::../constraints/top.sdc"
    ] @ (if List.is_empty resolved.macros then [] else
    [ "MACROS", macro_configuration resolved
    ; "EXTRA_VERILOG_MODELS", `List [string ("dir::../" ^ macro_stub_path)]
    ; "STA_CORNERS", list (fun (corner, _) -> string corner) Resolved.macro_corners
    ; "DEFAULT_CORNER", string "nom_typ_1p20V_25C"
    ]) @ Option.value_map resolved.physical_plan ~default:[] ~f:Macro_physical_plan.settings
;;

let config (resolved : Resolved.t) source_name =

  (* Resolved settings as (key, value), with only the floorplan path rewritten *)
  let settings =
    List.map resolved.settings ~f:(fun setting ->
      let value =
        if String.equal setting.key "FP_DEF_TEMPLATE"
        then `String ("dir::../tt/" ^ resolved.target.floorplan.path)
        else setting.value
      in
      setting.key, value)
  in

  (* Settings that point at files this bundle emits *)
  let generated =
    generated_settings resolved source_name
  in

  assoc
    (List.sort (settings @ generated) ~compare:(fun (a, _) (b, _) ->
       String.compare a b))
;;

(* One resource record as a manifest entry; identity as its string form, everything else
   as a sexp string (see [sexp]). The full decision chain is kept, request through source
   roles, so the manifest says WHY each resource was built the way it was;
*)
let resource_json (resource : Resource.Record.t) =
  assoc
    [ "id", string (Resource.Id.to_string resource.id)
    ; "request", sexp Resource.Request.sexp_of_t resource.request
    ; "requirement", sexp Resource.Policy.Requirement.sexp_of_t resource.requirement
    ; "policy", sexp Resource.Policy.Applied.sexp_of_t resource.policy
    ; "selection", sexp Resource.Selection.sexp_of_t resource.selection
    ; "elaborated_as", sexp Resource.Record.Elaborated_as.sexp_of_t resource.elaborated_as
    ; "source_roles", list (sexp Resource.Record.Source.sexp_of_t) resource.sources
    ]
;;

(* One external target reference (floorplan or PDK view) as a manifest entry; [sha256] and
   [corner] become JSON null when unknown, rather than being left out, so every entry has
   the same keys;
*)
let reference_json (reference : Platform.Target.Reference.t) =
  assoc
    [ "source", sexp Platform.Target.Reference.Source.sexp_of_t reference.source
    ; "revision", string reference.revision
    ; "role", sexp Platform.Target.Reference.Role.sexp_of_t reference.role
    ; "path", string reference.path
    ; "sha256", Option.value_map reference.sha256 ~default:`Null ~f:string
    ; "corner", Option.value_map reference.corner ~default:`Null ~f:string
    ]
;;

(* One resolved setting as a manifest entry; unlike config.json, this keeps the [owner],
   so an override is recorded along with its reason;
*)
let setting_json (setting : Resolved.Setting.t) =
  assoc
    [ "key", string setting.key
    ; "value", setting.value
    ; "owner", sexp Resolved.Setting.Owner.sexp_of_t setting.owner
    ]
;;

let macro_json ~physical (m : Resolved.Macro.t) =
  assoc
    [ "resource", string (Resource.Id.to_string m.resource)
    ; "module", string m.name
    ; "instance", string m.instance
    ; "collateral", list (fun (c : Resource.Technology.Collateral.t) ->
        assoc [ "role", sexp Resource.Technology.Collateral.Role.sexp_of_t c.role
              ; "reference", string c.reference ]) m.collateral
    ; "physical_intent", Option.value_map m.intent ~default:`Null ~f:(fun i ->
        assoc [ "location_um", `List [`Float i.x_um; `Float i.y_um]
              ; "orientation", string "R0"; "halo_um", `Float i.halo_um
              ; "power", assoc ["VDD!", string "VPWR"; "VDDARRAY!", string "VPWR"; "VSS!", string "VGND"]
              ; "execution", string (if physical then "fresh_full" else "unsupported_pending_verified_power_geometry") ])
    ]
;;

(* Empty synthesis declaration, never a functional model. *)
let macro_stub = Flow.Stock_sram_synthesis.macro_stub

(* The main sauce here for bundles; given a resolved flow, produce every file of the
   bundle and a manifest that pins down where each byte came from;

   1. [simulation_project], if given, must be a Simulation elaboration of the same project and
      top module -> otherwise error;
   2. generate synthesis RTL from the resolved flow's elaborated project, and simulation
      RTL from [simulation_project] when there is one;
   3. dedup and sort [input_paths]; none left -> error;
   4. read git HEAD of [source_root] -> error when there is no repository or no commit;
   5. read every input's bytes (see [read_input]) and its git status -> every input that
      fails is reported in one combined error; an input whose read fails skips its git
      status;
   6. collect produced and copied files, sorted by path;
   7. build the manifest payload and hash it into the identity;
   8. prepend the identity to the payload, and append "manifest.json" to the files;

   Returns Or_error rather than raising since it reads the file system and runs git after
   elaboration is over, where a missing input is an ordinary user mistake; the example
   decides to [ok_exn]. Nothing is written on any path, success or failure;

   The identity is the SHA-256 of the compact payload, which is everything in the manifest
   except the identity itself. It covers the git revision, each input's git status and
   content hash, and every emitted file's hash, so the same design rendered from a
   different commit or a dirty tree gets a different identity -> see test/check_bundle.py.

   A consequence: the payload's field ORDER is part of the identity. Reordering fields,
   renaming one, or bumping a requested tool version below changes every identity even
   when no design changed; bump "schema_version" when that is intended.

   Careful: without [simulation_project], "simulation_sources" points at the synthesis RTL
   under src/, and "simulation_resources" is empty. A simulation consumer then compiles
   the synthesis RTL, without any simulation only behavior (the memory example's
   diagnostic initialisation, for one), and nothing says so apart from that path.
*)
let render
    ?simulation_project (* a separate Simulation elaboration of the same project; *)
    ?(timing_physical_qualification = false)
    ~source_root (* git checkout that [input_paths] are relative to; *)
    ~input_paths (* generator sources to copy and hash; order and repeats ignored; *)
    (resolved : Resolved.t)
  =
  let elaborated_project = resolved.elaborated_project in
  let top = Circuit.name (Elaborated_project.circuit elaborated_project) in
  let source_name = top ^ ".v" in

  let%bind.Or_error () =
    if timing_physical_qualification
       && (Option.is_none resolved.timing_intent || not (List.is_empty resolved.macros))
    then Or_error.error_string "physical timing qualification requires declared timing stages and no macros"
    else Ok ()
  in

  (* The simulation project must describe the same design, or the two RTL sets disagree *)
  let%bind.Or_error () =
    match simulation_project with
    | None -> Ok ()
    | Some simulation ->
      if Mode.equal (Elaborated_project.mode simulation) Simulation
         && String.equal
              (Elaborated_project.project_name simulation)
              (Elaborated_project.project_name elaborated_project)
          && String.equal (Circuit.name (Elaborated_project.circuit simulation)) top
          && Option.equal (fun a b -> String.equal (Hardware_variant.identity a) (Hardware_variant.identity b))
               (Elaborated_project.hardware_variant simulation)
               (Elaborated_project.hardware_variant elaborated_project)
      then Ok ()
      else
        Or_error.error_string
            "simulation project must have Simulation mode and the same project, top and hardware variant"
  in

  (* Synthesis RTL, one file holding the full module hierarchy *)
  let rtl =
    Rtl.create Verilog [ Elaborated_project.circuit elaborated_project ]
    |> Rtl.full_hierarchy |> Rope.to_string
  in

  (* Simulation RTL from its own elaboration; never meant to be compiled with [rtl] *)
  let simulation_rtl =
    Option.map simulation_project ~f:(fun simulation ->
      Rtl.create Verilog [ Elaborated_project.circuit simulation ]
      |> Rtl.full_hierarchy
      |> Rope.to_string)
  in

  (* config.json's value; also recorded in the manifest as "flow_configuration" *)
  let flow_configuration = config resolved source_name in

  (* Step 3; order and repeats in the declaration do not matter *)
  let input_paths = List.dedup_and_sort input_paths ~compare:String.compare in

  (* TODO (possible optimization): this check runs after both RTL sets are generated; it
     could move to the top, since it needs nothing computed above; *)
   (* TODO (future fix): this message and the simulation project one above use
     error_string with no fix, against docs/development/comment_guidelines.md section 9; see
     [read_input] for the other messages in this file; *)
  let%bind.Or_error () =
    if List.is_empty input_paths
    then Or_error.error_string "bundle needs at least one declared source input"
    else Ok ()
  in

  (* Step 4; the commit checked out, NOT where the inputs are read from, which is the
     working tree (so dirty files are copied as they are) *)
  let%bind.Or_error source_revision =
    run_git source_root [ "rev-parse"; "HEAD" ]
    |> Or_error.tag ~tag:"Bundle source provenance requires a Git checkout with an initial commit (HEAD)"
  in

  (* Step 5; (path, bytes, porcelain status) per input; status is "" for a clean tracked
     file;

     Careful: a file ignored by .gitignore also reports "", so it looks clean in the
     manifest. Its bytes are still copied and hashed, so the identity still covers it.

     TODO (possible optimization): one git status per input; a single call over all paths
     would do, if input lists grow;
  *)
  let%bind.Or_error inputs =
    Or_error.all
      (List.map input_paths ~f:(fun path ->
         let%bind.Or_error contents = read_input ~source_root path in
         let%map.Or_error status =
           run_git
             source_root
             [ "status"; "--porcelain"; "--untracked-files=all"; "--"; path ]
         in
         path, contents, status))
  in

  (* Files generated from the declaration; simulation/ only with a simulation project *)
  let produced =
    [ "src/" ^ source_name, rtl
    ; "src/config.json", json flow_configuration
    ; "constraints/top.sdc", sdc ~timing_physical_qualification resolved
     ; "info.yaml", info_yaml resolved source_name
     ]
      @ (if List.is_empty resolved.macros then [] else [macro_stub_path, macro_stub])
      @ Option.value_map resolved.timing_intent ~default:[] ~f:(fun _ ->
        [ Timing_coverage_tcl.path, Timing_coverage_tcl.render () ])
      @ Option.value_map resolved.physical_plan ~default:[] ~f:Macro_physical_plan.files
    @ Option.value_map simulation_rtl ~default:[] ~f:(fun rtl ->
      [ "simulation/" ^ source_name, rtl ])
  in

  (* Exact copies of the declared inputs; [valid_path] keeps them under inputs/ *)
  let copied = List.map inputs ~f:(fun (path, data, _) -> "inputs/" ^ path, data) in

  (* Every file except the manifest, sorted, so the inventory order is deterministic *)
  let files =
    List.sort (produced @ copied) ~compare:(fun (a, _) (b, _) -> String.compare a b)
  in

  (* Inventory entry for one emitted file *)
  let file_json (path, data) =
    assoc [ "path", string path; "sha256", string (sha256 data) ]
  in

  (* Provenance entry per input: where it came from, where the copy is, and its state *)
  let inputs_json =
    list
      (fun (path, contents, status) ->
        assoc
          [ "path", string path
          ; "copy", string ("inputs/" ^ path)
          ; "sha256", string (sha256 contents)
          ; "git_status", string status
          ])
      inputs
  in

  (* Where simulation consumers compile from; falls back to src/, see Careful above *)
  let simulation_source =
    (if Option.is_some simulation_rtl then "simulation/" else "src/") ^ source_name
  in

  (* Only [operation] is used; the adapter is always LibreLane here *)
  let flow = Elaborated_project.flow elaborated_project in

  (* Step 7; the manifest without its identity; see the header for why field ORDER
     matters;

     pdk_revision: the first view's revision. The [] arm is unreachable in practice, since
     Resolved.validate_capabilities requires the PDK configuration view for every
     operation; if views ever come from different PDK revisions, only the first is kept.

     librelane and python are the versions the bundle asks for, NOT versions that ran;
     run records are P4 (docs/guides/bundle-emission.md);
  *)
  let payload =
    assoc
      ([ "schema_version", `Int 1
      ; "project", string (Elaborated_project.project_name elaborated_project)
      ; "top_module", string top
      ; "source_revision", string source_revision
      ; "source_inputs", inputs_json
      ; "files", list file_json files
      ; "synthesis_sources", `List [ string ("src/" ^ source_name) ]
      ; "simulation_sources", `List [ string simulation_source ]
      ; "resources", list resource_json (Elaborated_project.resources elaborated_project)
      ; "resource_snapshot", Resource_snapshot.to_json (Resource_snapshot.of_resolved resolved)
      ; ( "simulation_resources"
        , Option.value_map simulation_project ~default:(`List []) ~f:(fun simulation ->
             list resource_json (Elaborated_project.resources simulation)) )
        ] @ Option.value_map (Elaborated_project.hardware_variant elaborated_project) ~default:[]
              ~f:(fun variant -> ["hardware_variant", Hardware_variant.to_yojson variant])
          @ Option.value_map resolved.timing_intent ~default:[] ~f:(fun intent ->
             ["timing_intent", Timing_intent.to_json ~physical:timing_physical_qualification intent;
              "timing_native_coverage", Timing_coverage_tcl.manifest ()])
        @ (if timing_physical_qualification then
             ["timing_physical_qualification", Timing_intent.physical_manifest ()] else [])
        @ (if List.is_empty resolved.macros then [] else
       [ "macro_contract", assoc
            ([ "schema_version", `Int (if Option.is_some resolved.physical_plan then 2 else 1)
            ; "execution", string (if Option.is_some resolved.physical_plan then "fresh_full" else "synthesis_only")
            ; "macros", list (macro_json ~physical:(Option.is_some resolved.physical_plan)) resolved.macros
           ; "blackbox_sources", `List [string macro_stub_path]
           ; "implementation_simulation", assoc
               [ "defines", `List [string "FUNCTIONAL"]
               ; "models", list string (List.concat_map resolved.macros ~f:(fun m ->
                   List.filter_map m.collateral ~f:(fun c -> match c.role with
                     | Simulation_model -> Some c.reference | _ -> None))
                   |> List.dedup_and_sort ~compare:String.compare)
               ]
           ; "physical_policy", assoc
               [ "geometry", string "pinned_cmos5l_klayout_real_gds_required"
               ; "lvs", string "cdl_consumption_and_extraction_scope_unverified"
               ; "magic_findings", string "retain_and_disposition_no_blanket_waiver"
               ; "electrical", string "preserve_liberty_limits_no_architectural_registers"
               ]
            ; "fast_temperature_difference", string "standard_cells_m40C_macro_m55C"
            ] @ Option.value_map resolved.physical_plan ~default:[] ~f:(fun plan ->
                ["physical_plan", Macro_physical_plan.to_json plan]))
       ]) @ [
         ( "target"
        , assoc
            [ "harness", sexp Platform.Harness.sexp_of_t resolved.target.harness
            ; "technology", string resolved.target.technology_name
            ; "tiles", string resolved.target.tiles
            ; "corner", string resolved.target.corner
            ; ( "external_references"
              , list reference_json (resolved.target.floorplan :: resolved.target.views) )
            ] )
      ; "adapter", string "LibreLane"
      ; "operation", sexp Flow.Config.Operation.sexp_of_t flow.operation
      ; "resolved_settings", list setting_json resolved.settings
      ; "flow_configuration", flow_configuration
      ; ( "generated_settings"
        , assoc
             (generated_settings resolved source_name) )
      ; ( "requested_tools"
        , assoc
            [ "support_tools_revision", string resolved.target.floorplan.revision
            ; ( "pdk_revision"
              , string
                  (match resolved.target.views with
                   | view :: _ -> view.revision
                   | [] -> "") )
            ; "librelane", string "3.1.0.dev3"
             ; "python", string "3.11"
             ; ( "runtime_policy"
               , assoc
                   [ "schema_version", `Int 1
                   ; ( "host"
                     , assoc
                         [ ( "python"
                           , assoc
                               [ "policy", string "exact-minor"
                               ; "version", string "3.11"
                               ] )
                         ] )
                   ; ( "implementation"
                     , assoc
                         [ "native", assoc [ "policy", string "host-runtime" ]
                         ; ( "dockerized"
                           , assoc
                               [ "policy", string "official-librelane-image"
                               ; "image_ref", string "ghcr.io/librelane/librelane:3.1.0.dev3"
                               ; ( "python"
                                 , assoc
                                     [ "policy", string "distribution-supported"
                                     ; "requires_python", string ">=3.10"
                                     ; ( "supported_minors"
                                       , list string [ "3.10"; "3.11"; "3.12"; "3.13"; "3.14" ] )
                                     ] )
                               ] )
                         ] )
                   ] )
             ] )
       ])
  in

  (* Hashed in compact form, so pretty printing of manifest.json never affects it *)
  let identity = sha256 (Yojson.Safe.to_string payload) in

  (* Step 8; the payload with "identity" as its first field *)
  let manifest =
    match payload with
    | `Assoc fields -> assoc (("identity", string identity) :: fields)
    | _ -> assert false (* can never happen; [payload] is built with [assoc] just above *)
  in

  (* manifest.json goes last and is NOT in its own inventory, since it holds the hashes *)
  Ok { files = files @ [ "manifest.json", json manifest ]; manifest; identity }
;;

(* grab one file's contents by bundle relative path; a linear scan, bundles are small *)
let file t path = List.Assoc.find t.files path ~equal:String.equal

(* grab every bundle relative path; sorted, except "manifest.json" which is always last *)
let paths t = List.map t.files ~f:fst

(* grab the manifest, identity included *)
let manifest t = t.manifest

(* grab the identity; the example prints this next to the output directory *)
let identity t = t.identity

(* "mkdir -p" for [path]; creates missing parents first, with mode 0o755;

   Careful: it only checks that [path] exists, not that it is a directory. A regular file
   in the way is not caught here; the next mkdir or write under it raises instead, which
   [write] turns into an error;
*)
let rec mkdir_p path =
  if not (Stdlib.Sys.file_exists path)
  then (
    mkdir_p (Filename.dirname path);
    U.mkdir path 0o755)
;;

(* Put a bundle on disk under [output_dir]; Or_error rather than raising since this is
   plain file system work at the end of a run, where the caller decides how to fail;

   not a directory : [output_dir] exists but is a file -> error;
   not empty       : [output_dir] has any entry -> error, nothing written;
   missing         : [output_dir] and its parents are created;

   The emptiness check is what guarantees no existing file is ever replaced; the writes
   themselves would happily truncate one. No path can escape [output_dir]: generated paths
   are fixed, and copied inputs passed [valid_path] in [render].

   Careful: writing is not atomic. A failure halfway (disk full, permissions) leaves a
   partial bundle behind, and since that directory is no longer empty, the next [write]
   to it refuses; delete it before retrying. A second writer racing between the check and
   the writes is not caught either.
*)
let write t ~output_dir =
  Or_error.try_with (fun () ->
    if Stdlib.Sys.file_exists output_dir
    then (
      if not (Stdlib.Sys.is_directory output_dir)
      then failwithf "bundle output is not a directory: %s" output_dir ();
      if Array.length (Stdlib.Sys.readdir output_dir) <> 0
      then failwithf "bundle output directory is not empty: %s" output_dir ());
    List.iter t.files ~f:(fun (path, data) ->
      let full = Filename.concat output_dir path in
      mkdir_p (Filename.dirname full);
      Out_channel.write_all full ~data))
;;

(* A successful publication; private in the interface so only publish can mint it; *)
module Publication = struct
  type t = { directory : string; reused : bool }
end

(* lstat preserves dangling links as collisions; only ENOENT means absent; *)
let publication_stat path =
  try Some (U.lstat path) with U.Unix_error (U.ENOENT, _, _) -> None
;;

(* Create directory ancestors without following links. EEXIST is a normal mkdir race,
   but its winner must have installed a real directory; *)
let rec publication_mkdir path =
  match publication_stat path with
  | Some stat when Poly.equal stat.U.st_kind U.S_DIR -> ()
  | Some _ ->
    raise_s [%message "bundle store ancestor is not a plain directory" (path : string)]
  | None ->
    publication_mkdir (Filename.dirname path);
    (try U.mkdir path 0o755 with U.Unix_error (U.EEXIST, _, _) -> ());
    (match publication_stat path with
     | Some stat when Poly.equal stat.U.st_kind U.S_DIR -> ()
     | _ ->
       raise_s [%message "bundle store directory creation collided" (path : string)])
;;

(* Check every ancestor, including those of an already existing store; *)
let rec publication_ancestors path =
  let parent = Filename.dirname path in
  if not (String.equal path parent) then publication_ancestors parent;
  publication_mkdir path
;;

(* Exact tree equality includes directories: an extra empty directory is corruption,
   as are links and special files. The manifest is compared as bytes too; *)
let verify_publication t root =
  let directories =
    List.concat_map t.files ~f:(fun (path, _) ->
      let rec parents path =
        let parent = Filename.dirname path in
        if String.equal parent "." then [] else parent :: parents parent
      in
      parents path)
    |> String.Set.of_list
  in
  let seen = ref String.Set.empty in
  let rec walk relative =
    let full = if String.is_empty relative then root else Filename.concat root relative in
    match (U.lstat full).U.st_kind with
    | U.S_DIR ->
      if not (String.is_empty relative || Set.mem directories relative)
      then raise_s [%message "unexpected bundle directory" (full : string)];
      Array.iter (Stdlib.Sys.readdir full) ~f:(fun name ->
        walk (if String.is_empty relative then name else Filename.concat relative name))
    | U.S_REG ->
      (match file t relative with
       | Some expected when String.equal expected (In_channel.read_all full) ->
         seen := Set.add !seen relative
       | Some _ -> raise_s [%message "bundle bytes differ" (full : string)]
       | None -> raise_s [%message "unexpected bundle file" (full : string)])
    | _ -> raise_s [%message "bundle member is not a plain file or directory" (full : string)]
  in
  walk "";
  List.iter t.files ~f:(fun (path, _) ->
    if not (Set.mem !seen path)
    then raise_s [%message "bundle file is missing" (root : string) (path : string)])
;;

(* Existing plain directories are supported. A publication link must name exactly
   one direct, identity-prefixed object; neither object nor members may be links; *)
let verify_destination t ~store_dir destination =
  let root =
    match (U.lstat destination).U.st_kind with
    | U.S_DIR -> destination
    | U.S_LNK ->
      let target = U.readlink destination in
      let name = Filename.basename target in
      if not (valid_path target)
         || not (String.equal target (Filename.concat ".objects" name))
         || not (String.is_prefix name ~prefix:(t.identity ^ "."))
      then raise_s [%message "bundle publication link escapes private object layout"
                              (destination : string) (target : string)];
      Filename.concat store_dir target
    | _ -> raise_s [%message "bundle identity destination is not a directory"
                            (destination : string)]
  in
  verify_publication t root
;;

(* Remove only this call's exclusively allocated, unpublished object. Never follow
   links during cleanup and never scan the store for old objects; *)
let rec remove_unpublished path =
  match (U.lstat path).U.st_kind with
  | U.S_DIR ->
    Array.iter (Stdlib.Sys.readdir path) ~f:(fun name ->
      remove_unpublished (Filename.concat path name));
    U.rmdir path
  | _ -> U.unlink path
;;

(* Atomic no-replace publication on local POSIX filesystems. rename cannot be used:
   the installed Unix API would allow replacing an existing empty directory.
   Exclusive symlink installs the public name only after complete verification; *)
let publish t ~store_dir =
  Or_error.try_with (fun () ->
    let absolute =
      if Filename.is_relative store_dir
      then Filename.concat (U.getcwd ()) store_dir
      else store_dir
    in
    publication_ancestors absolute;
    let store_dir = U.realpath absolute in
    let destination = Filename.concat store_dir t.identity in
    let objects = Filename.concat store_dir ".objects" in
    (* Validate the private parent even on reuse, if present. *)
    (match publication_stat objects with
     | None -> ()
     | Some _ -> publication_ancestors objects);
    match publication_stat destination with
    | Some _ ->
      verify_destination t ~store_dir destination;
      { Publication.directory = destination; reused = true }
    | None ->
      publication_ancestors objects;
      let rec allocate attempt =
        if attempt = 100 then failwith "unable to allocate private bundle object";
        let name = Printf.sprintf "%s.%d.%08x" t.identity (U.getpid ()) (Random.bits ()) in
        let stage = Filename.concat objects name in
        try U.mkdir stage 0o755; name, stage
        with U.Unix_error (U.EEXIST, _, _) -> allocate (attempt + 1)
      in
      let name, stage = allocate 0 in
      let published = ref false in
      Exn.protect
        ~f:(fun () ->
          write t ~output_dir:stage |> Or_error.ok_exn;
          verify_publication t stage;
          let reused =
            try
              U.symlink (Filename.concat ".objects" name) destination;
              published := true;
              false
            with U.Unix_error (U.EEXIST, _, _) ->
              verify_destination t ~store_dir destination;
              true
          in
          { Publication.directory = destination; reused })
        ~finally:(fun () -> if not !published then remove_unpublished stage))
  |> Or_error.tag ~tag:"bundle publication failed"
;;

[@@@ocamlformat "enable"]
