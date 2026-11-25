(* Keep the renderer independent of the compiler, filesystem, and flow runtime. *)
let valid_name s =
  let lower c = c >= 'a' && c <= 'z' in
  String.length s >= 1
  && String.length s <= 64
  && lower s.[0]
  && String.for_all (fun c -> lower c || (c >= '0' && c <= '9') || c = '_' || c = '-') s
;;

let valid_source_name s = valid_name s && not (String.contains s '-')
let mapping = "ui_in:uo_out:comb8"

let usage =
  "usage: hardcaml-asic-starter render --name NAME (Dune root and first chip target)\n\
  \       hardcaml-asic-starter render-target --name NAME (chip target only)\n\
  \       hardcaml-asic-starter render-target --name NAME --library ip_NAME --mapping \
   ui_in:uo_out:comb8\n\
  \         Eight-bit combinational Block.create: ui_in to uo_out; clock/reset unused.\n\
  \       hardcaml-asic-starter render-ip --name NAME --template empty|sample\n\
  \       hardcaml-asic-starter render-module --name NAME\n\
  \       hardcaml-asic-starter describe-flow-authoring\n\
  \       hardcaml-asic-starter render-flow|render-profile 'JSON REQUEST'\n\
   Target names match [a-z][a-z0-9_-]{0,63}; IP and module names match \
   [a-z][a-z0-9_]{0,63}."
;;

let target_files ?library name =
  let dir = "asic_" ^ name in
  (* Preserve the external id verbatim; privately escape '-' and '_' for
     Dune/OCaml/Verilog so a-b and a_b can coexist without a name collision. *)
  let id =
    String.concat
      "_h"
      (String.split_on_char '-' (String.concat "_u" (String.split_on_char '_' name)))
  in
  let chip_lib = "chip_target_" ^ id in
  let flows = chip_lib ^ "_flows" in
  let consumer contents =
    Printf.sprintf
      "module Chip = %s.Chip\n\n%s"
      (String.capitalize_ascii chip_lib)
      contents
  in
  let design, description, output, assumptions, ip_sources =
    match library with
    | None ->
      ( {|let spec = Signal.Reg_spec.create ~clock:i.clk ~clear:(Signal.(~:) i.rst_n) () in
    let data = Signal.reg spec i.ui_in in|}
      , "Eight-bit register"
      , "Registered data"
      , "On the rising edge of clk, capture ui_in on uo_out; low rst_n synchronously \
         clears it."
      , [] )
    | Some lib ->
      let ip = "ip/" ^ String.sub lib 3 (String.length lib - 3) ^ "/" in
      ( Printf.sprintf
          "(* Combinational: clk and rst_n are intentionally unused. *)\n\
          \    let data = %s.Block.create i.ui_in in"
          (String.capitalize_ascii lib)
      , "Eight-bit combinational IP"
      , "Combinational IP output"
      , Printf.sprintf
          "ui_in[7:0] -> %s.Block.create -> uo_out[7:0]. The IP uses neither clk nor \
           rst_n."
          lib
      , List.map (( ^ ) ip) [ "dune"; "block.ml"; "chip-interface.json" ] )
  in
  let sources =
    ("dune-project" :: List.map (Filename.concat dir) [ "dune"; "chip.ml"; "project.ml" ])
    @ ip_sources
    |> List.map (Printf.sprintf "%S")
    |> String.concat "; "
  in
  let entries =
    [ ( "README.md"
      , Printf.sprintf
          {|# %s

Tiny Tapeout 6×4, IHP SG13CMOS5L. Harness clock constraint: clk, 20 ns.
%s Unused bidirectional pins are disabled. Edit `chip.ml` and `test_chip.ml` here.

With `hardcaml_asic` installed in your active OCaml switch:

```sh
dune build %s/project.exe
dune runtest %s
dune exec ./%s/rtl.exe > chip.v
dune exec ./%s/project.exe -- chip list --json
dune exec ./%s/project.exe -- flow list --json
```

Run from the enclosing project root. RTL and simulation need no PDK.

## Preview and reproduce synthesis

Planning records Git provenance. Before planning a newly created project,
make an initial Git commit with `dune-project`, this chip's source directory
(including `flows/`), any referenced IP, and `hardcaml-asic-targets.json` if
present. Do not commit `_build/`, `_flow_results/`, `_flow_tools`, `.dev/` or
local Silver state. For headless replay, start from a clean clone of the same
commit. Silver and its `.silver/` configuration are not needed.

Use the same OCaml switch, installed `hardcaml_asic` tools and libraries, and
provisioned toolchain for both runs. Set `ASIC_TOOLCHAIN` to its absolute path.
Provision with `hardcaml-asic-flow provision --toolchain "$ASIC_TOOLCHAIN"` or
add `--check` to verify existing tools without provisioning again. The commands
below also require `jq` and a reachable Docker daemon with the pinned LibreLane
image. If launching through Silver instead, its generated configuration
expects `_flow_tools` in the project root; a symlink to the provisioned
toolchain is sufficient. Build the chip executable above before previewing.

From the enclosing project root, preview and then explicitly run synthesis:

```sh
set -eu
chip=./_build/default/%s/project.exe
run=$(mktemp -d)
tools=${ASIC_TOOLCHAIN:?Set ASIC_TOOLCHAIN to the provisioned toolchain}
set -- --flow standard --profile balanced --endpoint synthesis --json

"$chip" flow plan "$@" > "$run/plan.json"
plan=$(jq -er '.plan_identity' "$run/plan.json")

# Inspect plan.json before continuing. Only 'stages' below launches EDA.
"$chip" flow emit "$@" --expected-plan-identity "$plan" \
  --results-root "$run" > "$run/emission.json"
bundle=$(jq -er '.output_dir' "$run/emission.json")
hardcaml-asic-flow stages "$bundle" \
  --methodology-plan "$run/plan.json" --expected-plan-identity "$plan" \
  --workspace "$run/synthesis" --checkpoint-store "$run/synthesis/checkpoints" \
  --python "$tools/.venv/bin/python" --support-tools "$tools/tt" \
  --pdk-root "$tools/pdk" --json > "$run/execution.jsonl"

execution=$(jq -ner '[inputs | select(.status == "completed" and .execution_file)]
  | last | .execution_file' "$run/execution.jsonl")
hardcaml-asic-flow report "$execution" --json > "$run/report.json"
jq -er 'select(.execution.status == "completed" and .completion.physical == "synthesis")
  | .checkpoints[] | select(.reference.boundary == "synthesis")
  | .metrics[] | select(.raw_key == "design__instance__count" and .availability == "available")
  | .value' "$run/report.json"
```

Compare mapped cell counts under the same toolchain, not execution IDs. A
completed synthesis is not physical signoff.
|}
          name
          assumptions
          dir
          dir
          dir
          dir
          dir
          dir )
    ; ( "dune"
      , Printf.sprintf
          {|(library
 (name %s)
 (modules chip)
 (libraries core hardcaml hardcaml_asic %s%s))

(test
 (name test_chip)
 (modules test_chip)
 (libraries %s core hardcaml hardcaml_asic%s))

(executable
 (name rtl)
 (modules rtl)
 (libraries %s core hardcaml hardcaml_asic))

(executable
 (name project)
 (modules project)
 (libraries %s core core_unix.sys_unix hardcaml_asic unix yojson))
|}
          chip_lib
          flows
          (Option.fold ~none:"" ~some:(( ^ ) " ") library)
          chip_lib
          (Option.fold ~none:"" ~some:(( ^ ) " ") library)
          chip_lib
          chip_lib )
    ; ( "chip.ml"
      , Printf.sprintf
          {|open Core
open Hardcaml
open Hardcaml_asic

module Design = struct
  module I = Tt_cmos5l.I
  module O = Tt_cmos5l.O

  let name = "tt_um_%s"

  let create _context (i : _ I.t) =
    %s
    { O.uo_out = data
    ; uio_out = Signal.zero 8
    ; uio_oe = Signal.zero 8
    }
end

let pinout =
  List.concat_map [ Pinout.Bank.Ui; Uo; Uio ] ~f:(fun bank ->
    let description =
      match bank with Ui -> "Data input" | Uo -> %S | Uio -> "Unused"
    in
    List.init 8 ~f:(fun bit -> { Pinout.bank; bit; description }))

let target_id = %S

let chip_target =
  Hardcaml_asic.Chip_target.create
    ~source_inputs:(%s.Generated.source_inputs @ [%s])
    ~name:"%s"
    ~metadata:{ title = "%s"; author = "Starter"; description = %S }
    ~design:(module Design)
    ~target:{ harness = Tiny_tapeout { tiles = T6x4 }; technology = Technology.ihp_sg13cmos5l }
    ~flow:(Flow.librelane ~overrides:(Tt_cmos5l.overrides ()) Hardening)
    ~clocks:[ { Clock.port = "clk"; period = Time_float.Span.of_ns 20. } ]
    ~pinout
    ~policy:(Resource_policy.create ~default:Flops [] |> Or_error.ok_exn)
    ()
  |> Or_error.ok_exn
|}
          id
          design
          output
          name
          (String.capitalize_ascii flows)
          sources
          id
          name
          description )
    ; "project.ml", consumer ("module Generated = " ^ String.capitalize_ascii flows ^ ".Generated\n" ^ Project_template.contents)
    ; "flows/dune", Collection.stanza ~root:(dir ^ "/flows") ~library:flows
    ; "flows/flow_standard.ml", Flow_authoring.flow_source ~id:"standard" ~profile:"balanced" ~endpoints:"Flow.Stage.all"
    ; ( "rtl.ml"
      , consumer
          {|open Core
open Hardcaml
open Hardcaml_asic

let () =
  let resolved = Chip_target.elaborate_for_flow Chip.chip_target |> Or_error.ok_exn in
  Rtl.create Verilog [ Elaborated_project.circuit resolved.elaborated_project ]
  |> Rtl.full_hierarchy
  |> Rope.to_string
  |> print_string
|}
      )
    ; ( "test_chip.ml"
      , consumer
          ({|open Core
open Hardcaml
open Hardcaml_asic

let () =
  let elaborated = Chip_target.elaborate Chip.chip_target ~mode:Simulation |> Or_error.ok_exn in
  let sim = Cyclesim.create (Elaborated_project.circuit elaborated) in
|}
           ^
           match library with
           | None ->
             {|
  let step reset_n data expected =
    Cyclesim.in_port sim "rst_n" := Bits.of_int_trunc ~width:1 reset_n;
    Cyclesim.in_port sim "ui_in" := Bits.of_int_trunc ~width:8 data;
    Cyclesim.cycle sim;
    let actual = !(Cyclesim.out_port ~clock_edge:After sim "uo_out") in
    if not (Bits.equal actual (Bits.of_int_trunc ~width:8 expected))
    then failwithf "expected %d, got %s" expected (Bits.to_string actual) ()
  in
  step 1 0xa5 0xa5;
  step 0 0xff 0;
  step 1 0x3c 0x3c
|}
           | Some lib ->
             Printf.sprintf
               {|
  let input = Signal.input "input" 8 in
  let reference = Signal.output "result" (%s.Block.create input) in
  let ref_sim = Cyclesim.create (Circuit.create_exn ~name:"reference" [reference]) in
  for n = 0 to 255 do
    let data = Bits.of_int_trunc ~width:8 n in
    Cyclesim.in_port sim "ui_in" := data;
    Cyclesim.in_port ref_sim "input" := data;
    Cyclesim.cycle sim;
    Cyclesim.cycle ref_sim;
    (* Before the active edge: a mistakenly registered wrapper must fail. *)
    let output s port = !(Cyclesim.out_port ~clock_edge:Before s port) in
    assert (Bits.equal (output sim "uo_out") (output ref_sim "result"));
    assert (Bits.equal (output sim "uio_oe") (Bits.zero 8));
    assert (Bits.equal (output sim "uio_out") (Bits.zero 8))
  done
|}
               (String.capitalize_ascii lib)) )
    ]
  in
  List.map (fun (path, contents) -> Filename.concat dir path, contents) entries
;;

let empty_module =
  "(* Add the interface and implementation of your hardware block here. *)\n"
;;

let ip_files name template =
  let library = "ip_" ^ name in
  let block, test =
    match template with
    | `Empty -> empty_module, "(* Add tests for your block here. *)\nlet () = ()\n"
    | `Sample ->
      ( "(* Invert each bit of the input bus. *)\n\
         let create data = Hardcaml.Signal.(~:) data\n"
      , Printf.sprintf
          {|open Hardcaml

let () =
  let data = Signal.input "data" 8 in
  let output = Signal.output "inverted" (%s.Block.create data) in
  let sim = Cyclesim.create (Circuit.create_exn ~name:"inverter" [ output ]) in
  List.iter (fun input ->
    Cyclesim.in_port sim "data" := Bits.of_int_trunc ~width:8 input;
    Cyclesim.cycle sim;
    let actual = !(Cyclesim.out_port ~clock_edge:After sim "inverted") in
    assert (Bits.equal actual (Bits.of_int_trunc ~width:8 (input lxor 0xff))))
    [ 0x00; 0xa5; 0xff ]
|}
          (String.capitalize_ascii library) )
  in
  ([ "dune", Printf.sprintf "(library\n (name %s)\n (libraries hardcaml))\n" library
   ; "block.ml", block
   ; ( "test/dune"
     , Printf.sprintf "(test\n (name test_block)\n (libraries %s hardcaml))\n" library )
   ; "test/test_block.ml", test
   ]
   @
   match template with
   | `Empty -> []
   | `Sample ->
     [ ( "chip-interface.json"
       , Yojson.Basic.pretty_to_string
           (`Assoc
             [ "schema_version", `Int 1
             ; "library", `String library
             ; "entrypoint", `String "Block.create"
             ; "mapping", `String mapping
             ; "clock", `String "unused"
             ; "reset", `String "unused"
             ])
         ^ "\n" )
     ])
  |> List.map (fun (path, contents) -> "ip/" ^ name ^ "/" ^ path, contents)
;;

let render ?(extra = []) entries =
  let file (path, contents) =
    `Assoc [ "path", `String path; "contents", `String contents ]
  in
  `Assoc
    ([ "schema_version", `Int 1
    ; "provider", `String "hardcaml_asic"
    ; "version", `String "0.1.0"
    ; "files", `List (List.map file entries)
    ] @ extra)
  |> Yojson.Basic.to_string
  |> print_endline
;;

let () =
  match Array.to_list Sys.argv with
  | _ :: "generate-collection" :: root :: paths -> Collection.generate root paths
  | [_; "describe-flow-authoring"] -> print_endline (Yojson.Safe.to_string Flow_authoring.describe)
  | [_; ("render-flow" | "render-profile" as command); json] ->
    (try let files, extra = Flow_authoring.render ~valid_name command json in render ~extra files
     with e -> prerr_endline (Printexc.to_string e); exit 2)
  | [ _; ("--help" | "-h") ] -> print_endline usage
  | [ _; "render"; "--name"; name ] when valid_name name ->
    render
      (("dune-project", Printf.sprintf "(lang dune 3.17)\n(name %s)\n" name)
       :: target_files name)
  | [ _; ("render-target" | "render-existing"); "--name"; name ] when valid_name name ->
    render (target_files name)
  | [ _; "render-target"; "--name"; name; "--library"; library; "--mapping"; m ]
    when valid_name name
         && String.starts_with ~prefix:"ip_" library
         && valid_source_name (String.sub library 3 (String.length library - 3))
         && m = mapping -> render (target_files ~library name)
  | [ _; "render-ip"; "--name"; name; "--template"; (("empty" | "sample") as template) ]
    when valid_source_name name ->
    render (ip_files name (if template = "empty" then `Empty else `Sample))
  | [ _; "render-module"; "--name"; name ] when valid_source_name name ->
    render [ name ^ ".ml", empty_module ]
  | _ ->
    prerr_endline usage;
    exit 2
;;
