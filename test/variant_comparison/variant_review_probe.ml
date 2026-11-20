open! Core
open Hardcaml
open Hardcaml_asic
module H = Hardware_variant
module J = Flow.Plan_json
let ok = Or_error.ok_exn
let obj xs = `Assoc xs
let fields = function `Assoc xs -> xs | _ -> failwith "object"
let set j k v = obj ((k,v)::List.Assoc.remove (fields j) ~equal:String.equal k)
let remove j k = obj (List.Assoc.remove (fields j) ~equal:String.equal k)
let variant depth =
  let parameter key kind = H.Parameter.create ~key ~kind |> ok in
  let schema = H.Schema.create ~design_id:"review" ~schema_id:"review.v1" ~parameters:[
    parameter "encoding" (Enumeration ["one_hot";"binary"]);
    parameter "depth" (Integer {minimum= -1_000_000_000;maximum=1_000_000_000});
    parameter "enabled" Boolean] |> ok in
  H.create ~schema ~assignments:["enabled",Bool true;"depth",Int depth;"encoding",Enum "binary"] |> ok
let resign_manifest j = let payload=remove j "identity" in
  set payload "identity" (`String (J.sha256 (Yojson.Safe.to_string payload)))
let rebind plan manifest =
  let j=Methodology_plan.to_yojson plan in
  let j=set j "bundle_manifest" (`String (Yojson.Safe.to_string manifest))
    |> fun j -> set j "bundle_identity" (J.get manifest "identity") in
  let payload=remove j "plan_identity" in
  set j "plan_identity" (`String (J.identity ~contract:"hardcaml-asic.plan-identity" payload |> ok))
let checks=ref []
let check name condition = if not condition then failwith name else checks:=name::!checks
let reject name value = check name (Or_error.is_error value)
let design_calls=ref 0
let project ?(memory=false) requirement =
  let module D=struct
    module I=Tt_cmos5l.I
    module O=Tt_cmos5l.O
    let name="tt_um_independent_variant_review"
    let create context (i:_ I.t) =
      incr design_calls;
      let data=if memory then
        Single_port_ram.create context ~name:"program"
          (Single_port_ram.Config.create_exn ~width:16 ~depth:256 ~read_latency:1)
          ~clock:i.clk ~enable:i.ena ~write_enable:i.rst_n ~address:i.ui_in
          ~write_data:(Signal.concat_msb [i.ui_in;i.uio_in])
        |> fun data -> Signal.select data ~high:7 ~low:0
        else Signal.reg (Signal.Reg_spec.create ~clock:i.clk ()) i.ui_in in
      {O.uo_out=data;uio_out=Signal.zero 8;uio_oe=Signal.zero 8}
  end in
  Project.create ~name:"review" ~metadata:{title="Independent review";author="B";description="Read-only library review"}
    ~design:(module D) ~source_inputs:["dune-project"]
    ~target:{harness=Tiny_tapeout {tiles=T6x4};technology=Technology.ihp_sg13cmos5l}
    ~flow:(Flow.librelane ~overrides:(Tt_cmos5l.overrides ()) Synthesis)
    ~clocks:[{Clock.port="clk";period=Time_float.Span.of_ns 20.}]
    ~pinout:(List.concat_map [Pinout.Bank.Ui;Uo;Uio] ~f:(fun bank -> List.init 8 ~f:(fun bit ->
      {Pinout.bank;bit;description="review fixture pin"})))
    ~policy:(Resource_policy.create ~default:requirement [] |> ok) () |> ok
let catalog () =
  let method_=Flow.Methodology.create ~id:"standard" ~label:"Standard" ~description:"Review"
    ~backend_recipe:Librelane_classic_v1 ~endpoints:[Synthesis]
    ~configurable:Flow.Parameter.Key.all ~defaults:[] |> ok in
  let profile=Flow.Profile.create ~id:"area" ~label:"Area" ~description:"Review"
    ~assignments:[Flow.Parameter.synth_strategy Area_0;Flow.Parameter.synth_abc_buffering false] |> ok in
  let entry=Flow.Catalog.Entry.create ~methodology:method_ ~profiles:[profile] ~default_profile:"area" |> ok in
  Flow.Catalog.create ~entries:[entry] ~default_flow:"standard" ~default_profile:"area" |> ok
let integration root =
  let v=variant 128 and w=variant 256 in
  let plain=project Flops in
  let factory_calls=ref 0 in
  let bound=Project.of_variant ~variant:v ~create:(fun supplied ->
    incr factory_calls;check "factory receives physically identical checked assignment" (phys_equal supplied v);Ok plain) |> ok in
  check "factory once, design not elaborated" (!factory_calls=1 && !design_calls=0);
  check "project attaches physically identical assignment" (phys_equal (Option.value_exn (Project.hardware_variant bound)) v);
  check "base project remains unbound" (Option.is_none (Project.hardware_variant plain));
  List.iter [false;true] ~f:(fun raises ->
    let calls=ref 0 in
    reject "failure/exception returns Error" (Project.of_variant ~variant:v ~create:(fun supplied ->
      incr calls;check "failure factory same assignment" (phys_equal supplied v);
      if raises then failwith "intentional factory exception" else Or_error.error_string "intentional failure"));
    check "failure factory called exactly once" (!calls=1));
  List.iter [v;w] ~f:(fun variant ->
    let calls=ref 0 in
    reject "equal/conflicting attachment rejects" (Project.of_variant ~variant ~create:(fun _ -> incr calls;Ok bound));
    check "repeat callback exactly once" (!calls=1);
    check "repeat leaves original attachment intact" (phys_equal (Option.value_exn (Project.hardware_variant bound)) v));
  let strip = function Sexp.List xs -> Sexp.List (List.filter xs ~f:(function
    | Sexp.List [Sexp.Atom "hardware_variant";_] -> false | _ -> true)) | x -> x in
  check "describe legacy omission exact" (Sexp.equal (strip (Project.describe bound)) (Project.describe plain));
  check "describe runs no design" (!design_calls=0);
  let cat=catalog () in
  let methodology=Flow.Catalog.resolve cat ~endpoint:Synthesis () |> ok in
  let rendered=Project.render_result ~methodology ~include_simulation:false bound ~source_root:root |> ok in
  check "render constructs exactly one implementation" (!design_calls=1);
  check "elaboration exact attachment" (phys_equal (Option.value_exn (Elaborated_project.hardware_variant
    (Project.Rendered.resolution rendered).elaborated_project)) v);
  let bundle=Project.Rendered.bundle rendered in
  let plan=Methodology_plan.of_rendered rendered |> ok in
  check "plan does not rerender" (!design_calls=1);
  let clone=H.of_string (H.to_string v) |> ok in
  check "equal clone distinct object" (not (phys_equal clone v));
  let cloned=Project.of_variant ~variant:clone ~create:(fun _ -> Ok plain) |> ok in
  let sim=Project.elaborate cloned ~mode:Simulation |> ok in
  ignore (Bundle.render ~simulation_project:sim ~source_root:root ~input_paths:["dune-project"]
    (Project.Rendered.resolution rendered) |> ok : Bundle.t);
  check "equal declaration simulation clone accepted" true;
  List.iter [plain;Project.of_variant ~variant:w ~create:(fun _ -> Ok plain) |> ok] ~f:(fun other ->
    let sim=Project.elaborate other ~mode:Simulation |> ok in
    reject "absent/different simulation rejects" (Bundle.render ~simulation_project:sim ~source_root:root
      ~input_paths:["dune-project"] (Project.Rendered.resolution rendered)));
  let legacy=Project.render_result ~methodology ~include_simulation:false plain ~source_root:root |> ok in
  let stripped=Bundle.manifest bundle |> fun j -> remove j "hardware_variant" |> resign_manifest in
  check "legacy manifest exact bytes/hash" (String.equal (Yojson.Safe.to_string stripped)
    (Yojson.Safe.to_string (Bundle.manifest (Project.Rendered.bundle legacy))));
  let manifest=Bundle.manifest bundle in
  let mutated_source=let inputs=J.array (J.get manifest "source_inputs") in
    set manifest "source_inputs" (`List (List.map inputs ~f:(fun i -> set i "sha256" (`String (String.make 64 '0'))))) |> resign_manifest in
  reject "self-consistent outer hashes cannot hide source/copy hash mismatch"
    (Methodology_plan.of_yojson (rebind plan mutated_source));
  List.iter ["../outside";"/outside";"inputs/../../outside"] ~f:(fun copy ->
    let inputs=J.array (J.get manifest "source_inputs") in
    let unsafe=set manifest "source_inputs" (`List (List.map inputs ~f:(fun i -> set i "copy" (`String copy)))) |> resign_manifest in
    reject "self-consistent source-copy traversal rejects" (Methodology_plan.of_yojson (rebind plan unsafe)));
  let null=manifest |> fun j -> set j "hardware_variant" `Null |> resign_manifest in
  reject "plan null variant with coherent outer identities" (Methodology_plan.of_yojson (rebind plan null));
  let unknown=H.to_yojson v |> fun j -> set j "hidden" (`Bool true) in
  let bad=manifest |> fun j -> set j "hardware_variant" unknown |> resign_manifest in
  reject "plan unknown inner fields with coherent outer identities" (Methodology_plan.of_yojson (rebind plan bad));
  let valid_other=manifest |> fun j -> set j "hardware_variant" (H.to_yojson w) |> resign_manifest in
  let relabelled=Methodology_plan.of_yojson (rebind plan valid_other) |> ok in
  check "valid coherent relabelling remains metadata, not semantic proof"
    (String.equal (H.identity (Option.value_exn (Methodology_plan.hardware_variant relabelled))) (H.identity w));
  let prepare project=match Project.prepare_resources ~methodology project ~source_root:root with
    | Ok p -> p | Error e -> failwith (Yojson.Safe.to_string (Resource_preparation.Failure.to_json e)) in
  let other=Project.of_variant ~variant:w ~create:(fun _ -> Ok plain) |> ok in
  let a=prepare bound and b=prepare other and c=prepare plain and d=prepare cloned in
  ignore (Resource_preparation.of_string (Resource_preparation.to_string a) |> ok : Resource_preparation.t);
  check "preparation public reader roundtrip" true;
  check "canonical clone preparation identity stable" (String.equal (Resource_preparation.identity a) (Resource_preparation.identity d));
  check "ignored variant different config identity" (not (String.equal (Resource_preparation.configuration_identity a) (Resource_preparation.configuration_identity b)));
  check "ignored variant equal actual circuit identity" (String.equal (Resource_preparation.circuit_identity a) (Resource_preparation.circuit_identity b));
  check "legacy same circuit, separate config identity" (String.equal (Resource_preparation.circuit_identity a) (Resource_preparation.circuit_identity c)
    && not (String.equal (Resource_preparation.configuration_identity a) (Resource_preparation.configuration_identity c)));
  let prep=Resource_preparation.to_json a in
  check "preparation has no compiled-source or P6 attestation"
    (Poly.equal (J.get (J.get prep "coverage") "compiled_source_proven") (`Bool false)
      && Poly.equal (J.get (J.get prep "coverage") "p6_source_stamp") (`Bool false));
  (* Existing preparation declarations are opaque tagged S-expressions, not a
     typed Hardware_variant reader. Characterize this honestly rather than
     treating a self-consistently rehashed declaration as execution attestation. *)
  let module PJ=Elaboration.Preparation_json in
  let malformed="{\"contract\":\"hardcaml-asic.hardware-variant\",\"schema_version\":999}" in
  reject "malformed preparation claim rejected by typed hardware codec" (H.of_string malformed);
  let rec replace = function
    | Sexp.List [Atom "hardware_variant";_] -> Sexp.List [Atom "hardware_variant";Atom malformed]
    | Sexp.List xs -> Sexp.List (List.map xs ~f:replace)
    | x -> x in
  let config=J.get prep "configuration" in
  let declaration=PJ.read_sexp (J.get config "declaration") |> replace |> PJ.sexp in
  let config=set config "declaration" declaration in
  let mutated=set prep "configuration" config
    |> fun j -> set j "configuration_identity" (`String (PJ.hash "configuration" config)) in
  let mutated=set mutated "identity" (`String (PJ.hash "preparation" (remove mutated "identity"))) in
  ignore (Resource_preparation.of_json mutated |> ok : Resource_preparation.t);
  check "opaque preparation claim is not typed hardware validation" true;
  let macro_plain=project ~memory:true Exact in
  let macro_bound=Project.of_variant ~variant:w ~create:(fun _ -> Ok macro_plain) |> ok in
  let results=List.map ["absent",macro_plain;"recorded",macro_bound] ~f:(fun (label,p) ->
    let rendered=Project.render_result ~methodology ~include_simulation:false p ~source_root:root |> ok in
    let manifest=Bundle.manifest (Project.Rendered.bundle rendered) in
    check ("Exact render succeeds: "^label) (List.Assoc.mem (fields manifest) "macro_contract" ~equal:String.equal);
    let result=Methodology_plan.prepare ~catalog:cat ~endpoint:Synthesis ~include_simulation:false p ~source_root:root in
    let plan=Methodology_plan.Prepared.plan (ok result) in
    check ("Exact unplaced named synthesis succeeds: "^label) (String.equal (Methodology_plan.project_name plan) "review");
    label,`String (Methodology_plan.plan_identity plan)) in
  obj ["status",`String "passed";"checks",`Int (List.length !checks);
    "controls",`List (List.map (List.rev !checks) ~f:(fun s -> `String s));"exact_prepare",obj results]
let () =
  let args=Array.to_list (Sys.get_argv ()) |> List.tl_exn in
  let result=match args with
    | ["sample"] -> H.to_yojson (variant 256)
    | ["decode"] ->
      let input=In_channel.input_all In_channel.stdin in
      (match H.of_string input with
       | Ok v -> obj ["accepted",`Bool true;"normalized",H.to_yojson v;"canonical",`String (H.to_string v)]
       | Error e -> obj ["accepted",`Bool false;"error",`String (Error.to_string_hum e)])
    | ["integration";root] -> integration root
    | _ -> failwith "sample | decode (stdin) | integration SOURCE_ROOT" in
  print_endline (Yojson.Safe.to_string result)
