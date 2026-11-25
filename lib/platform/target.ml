(* University of Florida *)
(* Author: Bohdan Purtell *)
(* Module: "platform/target.ml" *)
(* Source implementation of target selection and resolution. A target is the project's
   answer to "where is this design going?", and a resolved target is the answer to "what
   exactly does that place look like?";

   The declaration is two separately selected axes, a harness and a technology, handed to
   Project.create as one record. Project.elaborate takes [technology] out of it to create
   the Elaboration.Context, and Elaborated_project carries the whole record through. Only
   Project.elaborate_for_flow goes further: Elaboration.Resolved.resolve calls [resolve]
   on Elaborated_project.target; its Resolved.t supplies the target owned LibreLane
   settings (die area, floorplan, power pins, routing layer) and the references Bundle
   writes into its manifest;

   Resolution is a lookup, not a computation. Every fact in a Resolved.t was read off the
   pinned support-tools and PDK revisions by hand and written down here or in
   Technology.Cmos5l (see docs/reference/target-reference.md), so resolving the same pair twice
   gives the same record -> see test/test_target.ml;

   It does NOT open, fetch or hash any file; paths and hashes are recorded as given, and
   checking them against a real checkout is execution preflight. It does NOT look at the design
   either; TT top-level ports, metadata, pinout and timing are Elaboration.Resolved's job.
   Project.create still does not check the pair (see its TODO), so an unsupported pair is
   only caught when someone asks for a flow.
*)

open! Core

[@@@ocamlformat "disable"]

(* The selected pair;

   harness    : what the design is packaged into, and what its top level must look like;
   technology : what the design is built out of, and which macros exist;

   Careful: this is a declaration, NOT a resolved target. Any harness can be paired with
   any technology here, including combinations nobody supports; Project.elaborate accepts
   them, and only [resolve] (through Project.elaborate_for_flow) rejects them.
*)
type t =
  { harness    : Harness.t
  ; technology : Resource.Technology.t
  }
[@@deriving sexp_of]

(* An axis aligned rectangle in micrometres; the unit is in the name so it cannot be
   mistaken for DEF database units;

   x_min : left edge;
   y_min : bottom edge;
   x_max : right edge;
   y_max : top edge;

   Careful: nothing in the type stops [x_max] being below [x_min]. [validate_inputs]
   checks the die area it is handed, but the mli does NOT make the record private, so a
   rectangle built anywhere else is not checked at all.
*)
module Rectangle_um = struct
  type t =
    { x_min : float
    ; y_min : float
    ; x_max : float
    ; y_max : float
    }
  [@@deriving sexp_of]
end

(* One external file a resolved target points at, pinned to a revision;

   source   : which checkout [path] is relative to, and which repository [revision] names;
   revision : full Git commit of that checkout;
   role     : what the file is for;
   path     : relative to the root of the [source] checkout, never absolute;
   sha256   : expected content hash; only the floorplan carries one, PDK views are pinned
              by [revision] alone;
   corner   : operating corner; only the standard-cell Liberty view carries one;

   A reference is a promise about where a file should be, NOT evidence that it is there;
   nothing in this module reads [path] or checks [sha256], that is execution preflight.
   Bundle.reference_json writes these out as they are.
*)
module Reference = struct

  (* Where a referenced file comes from;

     Support_tools : the TinyTapeout tt-support-tools checkout; the floorplan DEF;
     Pdk           : the IHP Open PDK checkout; every technology view;
  *)
  module Source = struct
    type t = Support_tools | Pdk [@@deriving sexp_of]
  end

  (* What a referenced file is for;

     Floorplan_def     : the TT block DEF template the floorplan starts from;
     Technology_view r : one CMOS5L standard-cell view, with its own view role [r];

     Wraps Technology.Cmos5l.View.Role.t rather than copying its constructors, so a view
     role is declared once, in Technology.
  *)
  module Role = struct
    type t =
      | Floorplan_def
      | Technology_view of Resource.Technology.Cmos5l.View.Role.t
    [@@deriving sexp_of]
  end

  type t =
    { source   : Source.t
    ; revision : string
    ; role     : Role.t
    ; path     : string
    ; sha256   : string option
    ; corner   : string option
    }
  [@@deriving sexp_of]
end

(* Everything about the target that comes from outside this repository; what [resolve]
   needs that the Harness and Technology constructors do not say;

   support_tools_revision : full commit of tt-support-tools the floorplan comes from;
   pdk_revision           : full commit of the IHP Open PDK every view comes from;
   floorplan_path         : the 6x4 block DEF, relative to the support-tools checkout;
   floorplan_sha256       : expected hash of that DEF, lowercase hex;
   die_area_um            : die bounds; should agree with tile_sizes.yaml at
                            [support_tools_revision];
   technology_views       : the PDK views to reference, relative to the PDK checkout;
   constraints            : the timing and drive environment Bundle.sdc renders and
                            Elaboration.Resolved emits configuration keys for; read off
                            the PDK's own standard-cell LibreLane configuration at
                            [pdk_revision], see Technology.Cmos5l.Constraints;

   Transparent on purpose, so a caller pinning different revisions can start from
   [reference_cmos5l_6x4] and override fields with { base with ... } -> see
   test/test_target.ml; [validate_inputs] is what keeps a hand-built record honest.

   Careful: "should agree" above is not checked by anything. [validate_inputs] checks the
   shape of each field (hex, relative, finite), never whether a revision actually has
   that path, that hash or that die area. Inputs with a wrong hash or die area resolve
   fine; nothing can notice before execution preflight, the first step that opens the files.
*)
module Inputs = struct
  type t =
    { support_tools_revision : string
    ; pdk_revision           : string
    ; floorplan_path         : string
    ; floorplan_sha256       : string
    ; die_area_um            : Rectangle_um.t
    ; technology_views       : Resource.Technology.Cmos5l.View.t list
    ; constraints            : Resource.Technology.Cmos5l.Constraints.t
    }
  [@@deriving sexp_of]

  (* The adopted reference inputs, and the default for [resolve]; first taken from the
     consumer's tinytapeout/toolchain.lock, then the support-tools revision moved to the
     ihp-sg13cmos5l head of 2026-10-23 for the SRAM marker-layer precheck fix (the 6x4
     DEF is byte-identical across that move); inspected against the support-tools
     tile_sizes.yaml and the CMOS5L PDK config at these revisions (sources listed in
     docs/reference/target-reference.md);

     No checkout path or installed PDK is embedded here, only revisions and paths relative
     to them, so the same declaration means the same thing on any machine.
  *)
  let reference_cmos5l_6x4 =
    { support_tools_revision = "d66cf179e7bc4d296362ab7e2e3b344dc3c4f665"
    ; pdk_revision           = "2bbec755dc67ca3db0261c3d6163e15735d66710"
    ; floorplan_path         = "tech/ihp-sg13cmos5l/def/tt_block_6x4_pgvdd.def"
    ; floorplan_sha256       =
        "b46d9a0ee8352160e48dbc8312f092f985629061df736c7f46d58686535a76f4"
    ; die_area_um            = { x_min = 0.; y_min = 0.; x_max = 1289.28; y_max = 710.64 }
    ; technology_views       = Resource.Technology.Cmos5l.views
    ; constraints            = Resource.Technology.Cmos5l.constraints
    }
  ;;
end

(* The resolved target; everything the flow needs to know about where the design is going,
   with every external file pinned;

   harness                  : the declared harness, carried through unchanged;
   technology_name          : the PDK name ("ihp-sg13cmos5l");
   tiles                    : the tile size as TT writes it ("6x4");
   die_area_um              : die bounds, copied from Inputs;
   floorplan                : the DEF template, pinned by support-tools revision AND hash;
   harness_power_pins       : (power, ground) nets of the TT block ("VPWR", "VGND");
   standard_cell_power_pins : (power, ground) pins of the standard cells ("VDD", "VSS");
   routing_layers           : the layers the project may route on, lowest first;
   top_routing_layer        : the highest of [routing_layers]; the flow's routing maximum;
   corner                   : the nominal timing corner;
   views                    : every PDK view, in the order Inputs listed them;
   constraints              : the checked timing and drive environment, copied from
                              Inputs; Bundle.sdc renders it into constraints/top.sdc and
                              Elaboration.Resolved derives the matching LibreLane
                              settings;

   Careful: only [resolve] builds one in this library, but the mli does NOT make the type
   private, so a Resolved.t built by hand skips every check in this module.
*)
module Resolved = struct
  type t =
    { harness                  : Harness.t
    ; technology_name          : string
    ; tiles                    : string
    ; die_area_um              : Rectangle_um.t
    ; floorplan                : Reference.t
    ; harness_power_pins       : string * string
    ; standard_cell_power_pins : string * string
    ; routing_layers           : string list
    ; top_routing_layer        : string
    ; corner                   : string
    ; views                    : Reference.t list
    ; constraints              : Resource.Technology.Cmos5l.Constraints.t
    }
  [@@deriving sexp_of]
end

(* Does [value] have exactly [length] characters, all lowercase hex digits?; used for full
   Git commits (40) and SHA-256 hashes (64);

   Uppercase is rejected on purpose, so the same revision or hash cannot be spelled two
   ways in a resolved target.

   Careful: 40 means a SHA-1 Git repository. A revision from a SHA-256 Git repository has
   64 digits and is rejected as malformed.
*)
let is_hex_string ~length value =
  String.length value = length
  && String.for_all value ~f:(function
    | '0' .. '9' | 'a' .. 'f' -> true
    | _ -> false)
;;

(* Is [path] a plain relative path inside its checkout?;

   A blank path                          -> rejected;
   A leading "/" (absolute)              -> rejected;
   An empty segment ("a//b", "a/")       -> rejected;
   A "." or ".." segment ("a/../b")      -> rejected;

   Why: references are joined onto a checkout root later, so ".." or an absolute path
   could point outside the pinned revision entirely, and "." or "//" would give one file
   several spellings;

   Careful: the check is purely textual. Leading whitespace (" /abs", " a.def") and
   backslashes pass, and so does a symlink inside the checkout that points out of it;
   none of those are caught until the file is opened.
*)
let valid_relative_path path =
  not (String.is_empty (String.strip path))
  && not (String.is_prefix path ~prefix:"/")
  && List.for_all (String.split path ~on:'/') ~f:(fun part ->
    not (String.is_empty part)
    && not (String.equal part "..")
    && not (String.equal part "."))
;;

(* Everything wrong with a constraint environment, as one sentence each; empty when it is
   usable. Returning the reasons rather than a bool so [validate_inputs] can say which
   value is the problem, not just that one of seven is;

   driving cell    : both halves nonblank and free of "/", so LibreLane's "<cell>/<pin>"
                     spelling splits back into exactly these two;
   load            : finite and nonnegative; a negative load is not a capacitance;
   fanout          : positive; zero would forbid every net;
   uncertainty     : finite and nonnegative; a negative one would hand setup free time;
   clock transition: finite and nonnegative, same reason;
   derate          : finite, nonnegative and below 100, so the early multiplier
                     1 - derate/100 stays positive; at 100 every early path takes no time
                     at all, and above it delays go negative;

   NOT checked: whether [driving_cell] is a cell the technology's Liberty actually
   defines, or whether [driving_cell_pin] is one of its output pins. This module opens no
   file, so a cell that does not exist validates fine here and is first caught when
   OpenSTA reads the SDC; checking it against an installed PDK is execution preflight work.
*)
let constraint_problems (constraints : Resource.Technology.Cmos5l.Constraints.t) =
  let blank value = String.is_empty (String.strip value) in
  let slashed value = String.is_substring value ~substring:"/" in
  let named value = (not (blank value)) && not (slashed value) in
  let usable value = Float.is_finite value && Float.(value >= 0.) in
  List.filter_opt
    [ Option.some_if
        (not (named constraints.driving_cell && named constraints.driving_cell_pin))
        "driving cell and pin must be nonblank and contain no \"/\""

    ; Option.some_if
        (not (usable constraints.output_cap_load_ff))
        "output capacitive load must be finite and nonnegative"

    ; Option.some_if (constraints.max_fanout <= 0) "maximum fanout must be positive"

    ; Option.some_if
        (not (usable constraints.clock_uncertainty_ns))
        "clock uncertainty must be finite and nonnegative"

    ; Option.some_if
        (not (usable constraints.clock_transition_ns))
        "clock transition must be finite and nonnegative"

    ; Option.some_if
        (not
           (usable constraints.time_derating_percent
            && Float.(constraints.time_derating_percent < 100.)))
        "timing derate must be finite and at least 0% but below 100%"
    ]
;;

(* Check a set of target inputs before anything is built from them; returns Or_error
   rather than raising since [resolve] is itself an Or_error step that
   Project.elaborate_for_flow tags on the way out;

   revisions       : both revisions are 40 digit lowercase hex;
   floorplan path  : relative, AND exactly the pinned 6x4 DEF path;
   floorplan hash  : 64 digit lowercase hex;
   die area        : all four bounds finite, with a positive width and height;
   missing views   : every required view role is present; all missing roles are reported
                     together;
   duplicate views : no role appears twice; only the first repeat found is reported;
   malformed views : every view path is relative, the Liberty view is at exactly
                     Technology.Cmos5l.corner, and no other view has a corner; all bad
                     views are reported together;
   constraints     : the timing and drive environment is usable, see
                     [constraint_problems]; every problem is reported together;

   Every check runs even when others fail, so one call reports every problem.

   Careful: the floorplan path is compared against a fixed string, so a caller supplying
   their own inputs can move to other revisions but NOT to another DEF file. That string
   is what ties Inputs to the 6x4 tile, which is fine only because [resolve] runs this
   for T6x4 alone; the [valid_relative_path] half of the check is redundant while the
   string is pinned.

   Careful: the local open of Technology.Cmos5l.View shadows [t] and [Role], so
   [t list] in the malformed views message is a View.t list, NOT a Target.t list, and
   [Role] below is the view role, NOT Reference.Role.
*)
let validate_inputs (inputs : Inputs.t) =

  let open Resource.Technology.Cmos5l.View in

  (* The view roles every target needs; currently that is every role there is *)
  let required =
    [ Role.Pdk_configuration
    ; Role.Technology_lef
    ; Role.Standard_cell_lef
    ; Role.Standard_cell_gds
    ; Role.Standard_cell_verilog
    ; Role.Standard_cell_liberty
    ]
  in

  let roles = List.map inputs.technology_views ~f:(fun view -> view.role) in

  (* Which required roles have no view at all? *)
  let missing =
    List.filter required ~f:(fun role ->
      not (List.mem roles role ~equal:Role.equal))
  in

  let duplicates = List.find_a_dup roles ~compare:Role.compare in

  (* Which views have a bad path, or a corner that does not belong on them? *)
  let malformed_views =
    List.filter inputs.technology_views ~f:(fun view ->
      not (valid_relative_path view.path)
      || (Role.equal view.role Standard_cell_liberty
          && not (Option.equal String.equal view.corner (Some Resource.Technology.Cmos5l.corner)))
      || (not (Role.equal view.role Standard_cell_liberty)
          && Option.is_some view.corner))
  in

  let { Rectangle_um.x_min; y_min; x_max; y_max } = inputs.die_area_um in

  (* Checking with the above in a list format, fold any errors into the result;

     TODO (future fix): the first four messages are plain strings that do not say which
     value was wrong (which revision, what path), against the structured diagnostic convention; attach the offending fields as labelled [%message] fields instead.
     Changing them changes expect test output (test/test_target.ml), so update those in
     the same commit;
  *)
  Or_error.combine_errors_unit
    [ (if is_hex_string ~length:40 inputs.support_tools_revision
       && is_hex_string ~length:40 inputs.pdk_revision
       then Ok ()
       else Or_error.error_string "target inputs need full lowercase Git revisions")

    ; (if valid_relative_path inputs.floorplan_path
          && String.equal
               inputs.floorplan_path
               "tech/ihp-sg13cmos5l/def/tt_block_6x4_pgvdd.def"
       then Ok ()
       else
         Or_error.error_string
           "TT CMOS5L 6x4 target needs its pinned relative floorplan DEF path")

    ; (if is_hex_string ~length:64 inputs.floorplan_sha256
       then Ok ()
       else Or_error.error_string "target inputs need a floorplan SHA-256")

    ; (if Float.is_finite x_min
       && Float.is_finite y_min
       && Float.is_finite x_max
       && Float.is_finite y_max
       && Float.(x_max > x_min)
       && Float.(y_max > y_min)
       then Ok ()
       else
         Or_error.error_string
           "target inputs need a finite positive die area in micrometres")

    ; (if List.is_empty missing
       then Ok ()
       else
         Or_error.error_s
           [%message "missing required CMOS5L views" (missing : Role.t list)])

    ; (match duplicates with
       | None -> Ok ()
       | Some duplicate ->
         Or_error.error_s [%message "duplicate CMOS5L view role" (duplicate : Role.t)])

    ; (if List.is_empty malformed_views
       then Ok ()
       else
         Or_error.error_s
           [%message "malformed CMOS5L view references" (malformed_views : t list)])

    ; (match constraint_problems inputs.constraints with
       | [] -> Ok ()
       | problems ->
         Or_error.error_s
           [%message
             "unusable timing constraint environment; correct the values named below"
               (problems : string list)
               ~constraints:
                 (inputs.constraints : Resource.Technology.Cmos5l.Constraints.t)])
    ]
;;

(* The main sauce here for targets; given a declared pair, return its resolved target, or
   say why the pair or its inputs cannot be resolved; returns Or_error rather than raising
   since Elaboration.Resolved.resolve binds it ahead of its own checks, long after design
   construction, so there is an Or_error chain to join;

   Harness            Technology                        Inputs     Result
   Tiny_tapeout T6x4, ihp-sg13cmos5l with no mappings , valid   -> Resolved.t;
   Tiny_tapeout T6x4, ihp-sg13cmos5l with no mappings , invalid -> every input error;
   any other pair                                     , any     -> unsupported target;

   [inputs] defaults to [Inputs.reference_cmos5l_6x4]; a caller only passes it to pin
   different revisions, and it reaches here from Project.elaborate_for_flow through
   Elaboration.Resolved.resolve. Inputs are only validated once the pair is supported, so
   an unsupported pair reports that alone, even when [inputs] is broken too.

   Why the name AND the empty mapping list: Technology.t is private and create_exn
   refuses the name "ihp-sg13cmos5l", so the name alone already means
   Technology.ihp_sg13cmos5l; the mapping check keeps the guard honest if that
   reservation is ever dropped, since a CMOS5L technology with macros is not what the
   views and settings below describe -> see test/test_target.ml;

   Most resolved facts are constants of this one pair, not inputs: the tile string,
   "VPWR"/"VGND" from the pinned project.py, and the cell power pins, routing layers and
   corner from Technology.Cmos5l. A consequence: changing any of those takes a code
   change here or in Technology; passing different [inputs] cannot.
*)
let resolve ?(inputs = Inputs.reference_cmos5l_6x4) ({ harness; technology } : t) =
  match harness with

  (* The one supported pair: TT 6x4 on the reference CMOS5L technology *)
  | Tiny_tapeout { tiles = T6x4 }
    when String.equal technology.name Resource.Technology.Cmos5l.pdk ->

    (* Nothing below is built unless every input check passes *)
    let%map.Or_error () = validate_inputs inputs in

    (* The DEF template, pinned by revision AND hash *)
    let floorplan =
      { Reference.source = Support_tools
      ; revision         = inputs.support_tools_revision
      ; role             = Floorplan_def
      ; path             = inputs.floorplan_path
      ; sha256           = Some inputs.floorplan_sha256
      ; corner           = None
      }
    in

    (* Every PDK view, pinned by revision only; the corner carries over from the input *)
    let views =
      List.map inputs.technology_views ~f:(fun view ->
        { Reference.source = Pdk
        ; revision         = inputs.pdk_revision
        ; role             = Technology_view view.role
        ; path             = view.path
        ; sha256           = None
        ; corner           = view.corner
        })
    in

    { Resolved.harness
    ; technology_name          = Resource.Technology.Cmos5l.pdk
    ; tiles                    = Harness.Tiny_tapeout.Tiles.to_string T6x4
    ; die_area_um              = inputs.die_area_um
    ; floorplan
    ; harness_power_pins       = "VPWR", "VGND"
    ; standard_cell_power_pins = Resource.Technology.Cmos5l.standard_cell_power_pins
    ; routing_layers           = Resource.Technology.Cmos5l.routing_layers
    ; top_routing_layer        = Resource.Technology.Cmos5l.top_routing_layer
    ; corner                   = Resource.Technology.Cmos5l.corner
    ; views
    ; constraints              = inputs.constraints
    }

  (* Every other pair: other tile sizes on CMOS5L, and any other technology;

     TODO (future fix): the ~tiles label carries the whole Harness.t rather than the
     tiles, so it prints (tiles (Tiny_tapeout (tiles T1x1))); ~harness would say what it
     is. Changing it changes expect test output (test/test_target.ml), so update those
     in the same commit;
  *)
  | _ ->
    Or_error.error_s
      [%message
        "unsupported target combination; the reference resolver supports TT 6x4 with IHP \
         SG13 CMOS5L"
          ~tiles:(harness : Harness.t)
          ~technology:(technology.name : string)]
;;

[@@@ocamlformat "enable"]
