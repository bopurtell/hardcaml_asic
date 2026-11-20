(* Keep the renderer independent of the compiler, filesystem, and flow runtime. *)
let valid_name s =
  let lower c = c >= 'a' && c <= 'z' in
  String.length s >= 1
  && String.length s <= 64
  && lower s.[0]
  && String.for_all (fun c -> lower c || (c >= '0' && c <= '9') || c = '_' || c = '-') s
;;

let files ?(existing = false) name =
  let dir = "asic_" ^ name in
  let id =
    String.map
      (function
        | '-' -> '_'
        | c -> c)
      name
  in
  let base =
    [ ( "README.md"
      , Printf.sprintf
          {|# %s

This Tiny Tapeout design registers an eight-bit input on the rising edge of
`clk`. Driving `rst_n` low at the edge clears the register. Edit `chip.ml` to
change the circuit, clock, pin descriptions or ASIC target, and update
`test_chip.ml` to match.

With `hardcaml_asic` installed in your active OCaml switch:

```sh
dune build
dune runtest
dune exec ./rtl.exe > chip.v
```

The test simulates the declared project. The last command explicitly resolves
the implementation and prints Verilog. None of these steps needs a PDK.
|}
          name )
    ; "dune-project", Printf.sprintf "(lang dune 3.17)\n(name %s)\n" id
    ; ( "dune"
      , {|(library
 (name chip)
 (modules chip)
 (libraries core hardcaml hardcaml_asic))

(test
 (name test_chip)
 (modules test_chip)
 (libraries chip core hardcaml hardcaml_asic))

(executable
 (name rtl)
 (modules rtl)
 (libraries chip core hardcaml hardcaml_asic))
|}
      )
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
    let spec = Signal.Reg_spec.create ~clock:i.clk ~clear:(Signal.(~:) i.rst_n) () in
    { O.uo_out = Signal.reg spec i.ui_in
    ; uio_out = Signal.zero 8
    ; uio_oe = Signal.zero 8
    }
end

let pinout =
  List.concat_map [ Pinout.Bank.Ui; Uo; Uio ] ~f:(fun bank ->
    let description =
      match bank with Ui -> "Data input" | Uo -> "Registered data" | Uio -> "Unused"
    in
    List.init 8 ~f:(fun bit -> { Pinout.bank; bit; description }))

let project =
  Hardcaml_asic.Project.create
    %s
    ~name:"%s"
    ~metadata:{ title = "%s"; author = "Starter"; description = "Eight-bit register" }
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
          (if existing
           then
             Printf.sprintf
               "~source_inputs:[\"dune-project\"; %S; %S; %S]"
               (dir ^ "/dune")
               (dir ^ "/chip.ml")
               (dir ^ "/project.ml")
           else "")
          id
          name )
    ; ( "rtl.ml"
      , {|open Core
open Hardcaml
open Hardcaml_asic

let () =
  let resolved = Hardcaml_asic.Project.elaborate_for_flow Chip.project |> Or_error.ok_exn in
  Rtl.create Verilog [ Elaborated_project.circuit resolved.elaborated_project ]
  |> Rtl.full_hierarchy
  |> Rope.to_string
  |> print_string
|}
      )
    ; ( "test_chip.ml"
      , {|open Core
open Hardcaml
open Hardcaml_asic

let () =
  let project = Hardcaml_asic.Project.elaborate Chip.project ~mode:Simulation |> Or_error.ok_exn in
  let sim = Cyclesim.create (Elaborated_project.circuit project) in
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
      )
    ]
  in
  if not existing
  then base
  else
    ("project.ml", Project_template.contents) :: base
    |> List.filter_map (fun (path, contents) ->
      let file contents = Some (dir ^ "/" ^ path, contents) in
      match path with
      | "dune-project" -> None
      | "dune" ->
        file
          {|(executables
 (names project test_chip rtl)
 (libraries core core_unix.sys_unix hardcaml hardcaml_asic yojson))

(rule
 (alias runtest)
 (action (run %{exe:test_chip.exe})))
|}
      | "chip.ml" -> file (contents ^ Printf.sprintf "\nlet target_id = %S\n" name)
      | "README.md" ->
        file
          (Printf.sprintf
             "# %s\n\n\
              From the existing project root:\n\n\
              ```sh\n\
              dune build %s/project.exe\n\
              dune runtest %s\n\
              dune exec ./%s/project.exe -- flow list --json\n\
              dune exec ./%s/project.exe -- flow plan --endpoint synthesis --source-root \
              \"$PWD\" --json\n\
              ```\n\n\
              Edit `chip.ml` and `test_chip.ml` here. Build the executable and reopen \
              the project in Silver to discover the standard flow. Flow planning \
              requires a Git checkout with a HEAD for source provenance, but does not \
              require an installed PDK.\n"
             name
             dir
             dir
             dir
             dir)
      | _ -> file contents)
;;

let render ~existing name =
  let file (path, contents) =
    `Assoc [ "path", `String path; "contents", `String contents ]
  in
  `Assoc
    [ "schema_version", `Int 1
    ; "provider", `String "hardcaml_asic"
    ; "version", `String "0.1.0"
    ; "files", `List (List.map file (files ~existing name))
    ]
  |> Yojson.Basic.to_string
  |> print_endline
;;

let () =
  match Array.to_list Sys.argv with
  | [ _; (("render" | "render-existing") as command); "--name"; name ]
    when valid_name name -> render ~existing:(command = "render-existing") name
  | _ ->
    prerr_endline
      "usage: hardcaml-asic-starter render|render-existing --name NAME (NAME matches \
       [a-z][a-z0-9_-]{0,63})";
    exit 2
;;
