(* Wire contract for unplaced stock SRAM synthesis only. Physical schema 2 is admitted
   separately. All paths below come from the built-in mapping. *)
open! Core
module J = Plan_json

let macro_stub =
  "(* blackbox *)\n\
   module RM_IHPSG13_1P_256x16_c2_bm_bist (\n\
   input A_CLK, A_MEN, A_WEN, A_REN,\n\
   input [7:0] A_ADDR,\n\
   input [15:0] A_DIN,\n\
   input A_DLY,\n\
   output [15:0] A_DOUT,\n\
   input [15:0] A_BM,\n\
   input A_BIST_CLK, A_BIST_EN, A_BIST_MEN, A_BIST_WEN, A_BIST_REN,\n\
   input [7:0] A_BIST_ADDR,\n\
   input [15:0] A_BIST_DIN, A_BIST_BM\n\
   );\n\
   endmodule\n"
;;

let generated ~manifest:m ~contract:c =
  let require condition message =
    if not condition then J.fail ("named SRAM synthesis: " ^ message)
  in
  let expect value expected message = require (J.equal value expected) message in
  let request = Resource.Request.single_port_ram ~width:16 ~depth:256 ~read_latency:1 in
  let tech = Resource.Technology.ihp_sg13cmos5l in
  let macro =
    (Resource.Technology.exact_mapping tech request |> Option.value_exn).macro
  in
  let stock = macro.name in
  let stub = "macros/" ^ stock ^ ".blackbox.v" in
  let sources role =
    List.filter_map macro.collateral ~f:(fun c ->
      if Resource.Technology.Collateral.Role.equal c.role role
      then Some (`String c.reference)
      else None)
  in
  J.object_
    c
    ~fields:
      [ "schema_version"
      ; "execution"
      ; "macros"
      ; "blackbox_sources"
      ; "implementation_simulation"
      ; "physical_policy"
      ; "fast_temperature_difference"
      ];
  expect (J.get c "schema_version") (`Int 1) "schema 1 required";
  expect
    (J.get c "execution")
    (`String "synthesis_only")
    "synthesis-only contract required";
  expect (J.get m "operation") (`String "Synthesis") "Synthesis operation required";
  expect (J.get (J.get m "target") "technology") (`String tech.name) "CMOS5L required";
  expect
    (J.get (J.get m "flow_configuration") "PDK")
    (`String tech.name)
    "CMOS5L PDK required";
  expect (J.get c "blackbox_sources") (`List [ `String stub ]) "stock black box required";
  let files = J.array (J.get m "files") in
  require
    (List.exists files ~f:(fun f ->
       J.equal (J.get f "path") (`String stub)
       && J.equal (J.get f "sha256") (`String (J.sha256 macro_stub))))
    "noncanonical stock black box";
  require
    (not
       (List.exists files ~f:(fun f ->
          String.is_prefix (J.string (J.get f "path")) ~prefix:"physical/")))
    "physical inputs require schema 2";
  expect
    (J.get c "implementation_simulation")
    (`Assoc
      [ "defines", `List [ `String "FUNCTIONAL" ]
      ; "models", `List (List.sort (sources Simulation_model) ~compare:Poly.compare)
      ])
    "stock simulation models required";
  (* Bundle.verify_manifest_identity also binds these rows to the resolved resource
     snapshot and its verified RTL instances. Require that evidence. *)
  ignore (J.get m "resource_snapshot" : Yojson.Safe.t);
  let rows = J.array (J.get c "macros") in
  require (not (List.is_empty rows)) "empty macro contract";
  let resources =
    List.map (J.array (J.get m "resources")) ~f:(fun r -> J.string (J.get r "id"), r)
    |> Map.of_alist_exn (module String)
  in
  let collateral =
    List.map macro.collateral ~f:(fun c ->
      Resource.Technology.Collateral.Role.sexp_of_t c.role, c.reference)
    |> List.sort ~compare:Poly.compare
  in
  let ids =
    List.map rows ~f:(fun row ->
      J.object_
        row
        ~fields:[ "resource"; "module"; "instance"; "collateral"; "physical_intent" ];
      let id = J.string (J.get row "resource") in
      expect (J.get row "module") (`String stock) "unsupported macro";
      expect
        (J.get row "instance")
        (`String (Resource.Id.rtl_instance_name (Resource.Id.of_string_exn id)))
        "noncanonical instance";
      expect (J.get row "physical_intent") `Null "placement intent unsupported";
      let inputs =
        List.map
          (J.array (J.get row "collateral"))
          ~f:(fun c ->
            J.object_ c ~fields:[ "role"; "reference" ];
            Sexp.of_string (J.string (J.get c "role")), J.string (J.get c "reference"))
      in
      require
        (Poly.equal (List.sort inputs ~compare:Poly.compare) collateral)
        "stock collateral required";
      let resource = Map.find_exn resources id in
      require
        (Sexp.equal
           (Sexp.of_string (J.string (J.get resource "request")))
           (Resource.Request.sexp_of_t request))
        "exact 256x16 request required";
      id)
  in
  require
    (Option.is_none (List.find_a_dup ids ~compare:String.compare))
    "duplicate resource";
  let corners =
    [ "nom_fast_1p32V_m40C", "fast"
    ; "nom_slow_1p08V_125C", "slow"
    ; "nom_typ_1p20V_25C", "typ"
    ]
  in
  [ ( "MACROS"
    , `Assoc
        [ ( stock
          , `Assoc
              [ "instances", `Assoc []
              ; "lef", `List (sources Physical_abstract)
              ; "gds", `List (sources Physical_layout)
              ; "spice", `List (sources Spice_netlist)
              ; ( "lib"
                , `Assoc
                    (List.map corners ~f:(fun (key, corner) ->
                       key, `List (sources (Timing_model { corner })))) )
              ] )
        ] )
  ; "EXTRA_VERILOG_MODELS", `List [ `String ("dir::../" ^ stub) ]
  ; "STA_CORNERS", `List (List.map corners ~f:(fun (key, _) -> `String key))
  ; "DEFAULT_CORNER", `String "nom_typ_1p20V_25C"
  ]
;;
