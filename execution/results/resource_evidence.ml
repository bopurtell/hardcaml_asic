module E = Evidence_json
let get j k = match j with `Assoc xs -> Option.value (List.assoc_opt k xs) ~default:`Null | _ -> `Null
let fields = function `Assoc xs -> xs | _ -> invalid_arg "expected object"
let list = function `List xs -> xs | _ -> invalid_arg "expected array"
let text = function `String s when s<>"" -> s | _ -> invalid_arg "expected nonempty string"
let s x = `String x
let obj x = `Assoc x
let arr x = `List x
let unavailable reason = obj ["status",s "unavailable";"reason",s reason]
let unique label xs = if List.length xs<>List.length (List.sort_uniq String.compare xs) then invalid_arg ("duplicate "^label)
let instance (name,typ) = obj ["instance",s name;"module",s typ]
let reconcile ~expected ~instances ~known_standard_cells ~reason =
  let names=List.concat_map (fun r -> List.map (fun i -> text (get i "instance")) (list (get r "instances"))) expected in
  unique "expected instance" names;
  let types=List.map (fun r -> text (get r "module")) expected in
  let observed=Option.value instances ~default:[] in
  let unknown=List.filter (fun (_,t) -> not (List.mem t known_standard_cells || List.mem t types)) observed in
  let extras=List.filter (fun (n,t) -> List.mem t types && not (List.exists (fun r -> get r "module"=s t &&
    List.exists (fun i -> get i "instance"=s n) (list (get r "instances"))) expected)) observed in
  let duplicate=List.length observed<>List.length (List.sort_uniq String.compare (List.map fst observed)) in
  let coverage=instances<>None && unknown=[] && not duplicate && reason=None in
  let rows=List.map (fun r ->
    let typ=text (get r "module") in
    let wanted=List.map (fun i -> text (get i "instance")) (list (get r "instances")) in
    let actual=List.filter (fun (n,t) -> List.mem n wanted || t=typ && not (List.mem n names)) observed in
    let exact=List.sort compare actual=List.sort compare (List.map (fun n -> n,typ) wanted) in
    let status,why=if not coverage then "unresolved",Option.value reason ~default:(if duplicate then "duplicate instance identities" else if unknown<>[] then "unknown cell classification" else "instance evidence unavailable")
      else if exact then "matched","exact module, identity and count"
      else if List.exists (fun (n,t) -> List.mem n wanted && t<>typ || t=typ && not (List.mem n wanted)) actual then "unexpected","wrong module or instance identity"
      else "missing","required instance absent from complete observation" in
    obj ["resource_id",get r "resource_id";"expected",r;"realized",(if instances=None then unavailable why else obj ["instances",arr (List.map instance actual);"count",`Int (List.length actual)]);
      "status",s status;"reason",s why]) expected in
  obj ["resources",arr rows;"unexpected_instances",arr (List.map instance (extras@unknown));
    "status",s (if expected=[] && instances=None && reason=None then "not_applicable" else if coverage && extras=[] && List.for_all (fun r -> get r "status"=s "matched") rows then "pass" else "fail");
    "reason",(match reason with Some r -> s r | None -> if unknown<>[] then s "unknown cell classification; inventory coverage incomplete" else if duplicate then s "duplicate instance identities" else `Null)]

type reader = { root:string; mutable refs:E.t list; mutable bytes:int }
let file p = try (Unix.stat p).Unix.st_kind=Unix.S_REG with Unix.Unix_error ((Unix.ENOENT|Unix.ENOTDIR),_,_) -> false
let read r path format =
  if List.length r.refs>=1024 then invalid_arg "resource evidence source count bound exceeded";
  let path=if Filename.is_relative path then Filename.concat r.root path else path in
  let canonical=Unix.realpath path in
  if not (String.starts_with ~prefix:(r.root^"/") canonical) then invalid_arg ("evidence escapes selected run: "^path);
  let fd=Unix.openfile canonical [Unix.O_RDONLY;Unix.O_NONBLOCK] 0 in
  let ic=Unix.in_channel_of_descr fd in
  let raw=Fun.protect ~finally:(fun () -> close_in_noerr ic) (fun () ->
    let before=Unix.fstat fd in
    if before.st_kind<>Unix.S_REG || before.st_size>32*1024*1024 then invalid_arg "resource evidence source bound/type";
    r.bytes<-r.bytes+before.st_size;
    if r.bytes>128*1024*1024 then invalid_arg "resource evidence aggregate bound exceeded";
    let raw=really_input_string ic before.st_size in
    let after=Unix.fstat fd in
    if before.st_dev<>after.st_dev || before.st_ino<>after.st_ino || before.st_size<>after.st_size || before.st_mtime<>after.st_mtime || before.st_ctime<>after.st_ctime then invalid_arg "resource evidence changed during read";
    raw) in
  let relative=String.sub canonical (String.length r.root+1) (String.length canonical-String.length r.root-1) in
  let ref_=obj ["path",s relative;"sha256",s (Identity_json.sha256_bytes raw);"format",s format;"scope",s "retained observed bytes; integrity is not producer authentication"] in
  let ref_=match List.find_opt (fun old -> get old "path"=s relative) r.refs with
    | None -> r.refs<-r.refs@[ref_];ref_
    | Some old -> if get old "sha256"<>get ref_ "sha256" then invalid_arg "source changed between resource evidence reads";old in
  raw,ref_
let rec normalize = function
  | `Intlit n -> (try `Int (int_of_string n) with Failure _ -> `Intlit n)
  | `Assoc xs -> `Assoc (List.map (fun (k,v) -> k,normalize v) xs)
  | `List xs -> `List (List.map normalize xs)
  | x -> x
let json r p = let raw,ref_=read r p "json" in
  match E.parse ~reject_duplicates:true raw with Ok d -> normalize (E.value d),ref_ | Error e -> invalid_arg (p^": "^e)
let hash_source r path format =
  if List.length r.refs>=1024 then invalid_arg "resource evidence source count bound exceeded";
  let canonical=Unix.realpath (Filename.concat r.root path) in
  if not (String.starts_with ~prefix:(r.root^"/") canonical) then invalid_arg "physical artifact escapes selected run";
  let relative=String.sub canonical (String.length r.root+1) (String.length canonical-String.length r.root-1) in
  let digest=match Checkpoint.file_record canonical with Ok f -> f.sha256 | Error e -> invalid_arg e in
  let ref_=obj ["path",s relative;"sha256",s digest;"format",s format;"scope",s "retained observed bytes; integrity is not producer authentication"] in
  if not (List.exists (fun ref_ -> get ref_ "path"=s relative) r.refs) then r.refs<-r.refs@[ref_];ref_
let first r paths = List.find_opt (fun p -> file (Filename.concat r.root p)) paths
let require_path r paths = match first r paths with Some p -> p | None -> invalid_arg ("missing retained evidence: "^String.concat " or " paths)
let manifest_paths=["project/manifest.json";"manifest.json";"bundle/manifest.json"]
let numeric label = function
  | `Int n when n>=0 -> float_of_int n
  | `Float f when Float.is_finite f && f>=0. -> f
  | `Intlit v -> let f=float_of_string v in if Float.is_finite f && f>=0. then f else invalid_arg label
  | _ -> invalid_arg ("malformed/nonfinite measurement: "^label)
let sexp j = try Core.Sexp.of_string (text j) with _ -> invalid_arg "invalid legacy resource S-expression"
let sexp_field k j = match sexp j with
  | Core.Sexp.List xs -> (match List.filter_map (function Core.Sexp.List [Core.Sexp.Atom key;v] when key=k -> Some v | _ -> None) xs with
    | [v] -> v | _ -> invalid_arg ("missing/duplicate legacy selection "^k))
  | _ -> invalid_arg "invalid legacy resource record"
let legacy_budget details =
  match List.sort Core.Sexp.compare details with
  | [Core.Sexp.List [Atom "limit";Atom n];List [Atom "macro";Atom macro]] ->
    (match int_of_string_opt n with
     | Some limit when limit>=0 && string_of_int limit=n && String.trim macro<>"" -> macro,limit
     | _ -> invalid_arg "invalid legacy macro budget")
  | _ -> invalid_arg "invalid legacy budget reason fields"
let inventory manifest =
  Manifest_metadata.validate manifest;
  (match get manifest "macro_contract" with `Null -> () | c ->
    if not (List.mem (get c "schema_version") [`Int 1;`Int 2]) then invalid_arg "unsupported macro contract coverage");
  match get manifest "resource_snapshot" with
  | `Null ->
    let resources=match get manifest "resources" with `Null -> invalid_arg "historical resource inventory unavailable" | v -> list v in
    let macros=match get (get manifest "macro_contract") "macros" with `Null -> [] | v -> list v in
     let exhausted=ref [] and counts=Hashtbl.create 16 in
     let rows=List.map (fun r ->
      let id=text (get r "id") in
      if not (List.for_all Hardcaml_asic.Resource.Id.is_valid_component (String.split_on_char '/' id)) then invalid_arg "invalid legacy resource identity";
      ignore (Hardcaml_asic.Resource.Id.of_string_exn id);
      let impl=match sexp_field "implementation" (get r "selection") with
        | Core.Sexp.Atom "Flops" -> obj ["kind",s "flops"]
        | Core.Sexp.List [Core.Sexp.Atom "Macro";Core.Sexp.Atom name] -> obj ["kind",s "macro";"name",s name]
        | _ -> invalid_arg "unknown legacy implementation" in
      ignore (sexp (get r "request"));
      let requirement=sexp (get r "requirement") in
      if not (List.mem requirement [Core.Sexp.Atom "Flops";Core.Sexp.Atom "Exact";Core.Sexp.Atom "Exact_or_flop_fallback"]) then invalid_arg "unknown legacy resource requirement";
      ignore (sexp (get r "policy"));
      let reason=sexp_field "reason" (get r "selection") in
      let selected=get impl "kind"=s "macro" in
      (match selected,requirement,reason with
       | false,Core.Sexp.Atom "Flops",Core.Sexp.Atom "Explicit_flops"
       | false,Core.Sexp.Atom "Exact_or_flop_fallback",Core.Sexp.List [Core.Sexp.Atom "Flop_fallback";_]
       | true,Core.Sexp.Atom ("Exact"|"Exact_or_flop_fallback"),Core.Sexp.List [Core.Sexp.Atom "Exact_mapping";_] -> ()
       | false,Core.Sexp.Atom "Exact_or_flop_fallback",Core.Sexp.List (Core.Sexp.Atom "Budget_exhausted"::details) ->
         exhausted := legacy_budget details :: !exhausted
       | _ -> invalid_arg "inconsistent legacy requirement/selection reason");
      (if selected then let name=text (get impl "name") in
         Hashtbl.replace counts name (1 + Option.value (Hashtbl.find_opt counts name) ~default:0));
      let found=List.filter (fun m -> get m "resource"=s id) macros in
      let instances=if not selected then [] else match found with
        | [m] when get m "module"=get impl "name" -> [obj ["module",get m "module";"instance",get m "instance"]]
        | _ -> invalid_arg "legacy selected macro has no unique matching macro contract" in
      if selected && sexp (get r "elaborated_as")<>Core.Sexp.Atom "Selected_implementation" then invalid_arg "legacy macro is not implementation RTL";
      List.iter (fun i -> let canonical=Hardcaml_asic.Resource.Id.of_string_exn id |> Hardcaml_asic.Resource.Id.rtl_instance_name in
        if get i "instance"<>s canonical then invalid_arg "legacy macro instance is not canonical resource identity") instances;
      obj ["resource_id",s id;"requested_contract",get r "request";"selected_implementation",impl;"instances",arr instances;
        "selection",get r "selection";"requirement",get r "requirement";"policy",get r "policy";"inventory_record",r]) resources in
     List.iter (fun (macro,limit) ->
       if Option.value (Hashtbl.find_opt counts macro) ~default:0 <> limit then invalid_arg "legacy macro count disagrees with exhausted budget") !exhausted;
     List.iter (fun m -> if not (List.exists (fun r -> get r "resource_id"=get m "resource" && get (get r "selected_implementation") "name"=get m "module") rows) then invalid_arg "unowned legacy macro contract") macros;
    "legacy_resources_macro_contract",rows
  | snapshot ->
    (match Hardcaml_asic.Elaboration.Resource_snapshot.of_json snapshot with Ok _ -> () | Error _ -> invalid_arg "malformed resource_snapshot");
    if get snapshot "scope"<>s "resolved" || get snapshot "mode"<>s "implementation" then invalid_arg "resource_snapshot is not resolved implementation";
    let rows=List.map (fun r -> obj ["resource_id",get (get r "id") "display";"requested_contract",get r "request";
      "selected_implementation",get (get r "selection") "implementation";
      "instances",(match get (get r "actual_instances") "instances" with `Null -> arr [] | v -> v);
      "selection",get r "selection";"requirement",get r "requirement";"policy",get r "policy";"inventory_record",r]) (list (get snapshot "resources")) in
    let contract=match get (get manifest "macro_contract") "macros" with `Null -> [] | v -> list v in
    let declared=List.concat_map (fun r -> List.map (fun i -> get r "resource_id",get i "module",get i "instance") (list (get r "instances"))) rows in
    let contracted=List.map (fun m -> get m "resource",get m "module",get m "instance") contract in
    if List.sort compare declared<>List.sort compare contracted then invalid_arg "resource_snapshot/macro_contract mismatch";
    "resource_snapshot",rows
let expected rows = List.filter_map (fun r -> let impl=get r "selected_implementation" in
  if get impl "kind"=s "flops" then None else
  Some (obj ["resource_id",get r "resource_id";"module",get impl "name";"instances",get r "instances";
    "count",`Int (List.length (list (get r "instances")));"retention_requirement",s "required"])) rows

(* Restricted structural gate-netlist grammar. Unsupported behavioral constructs
   are unresolved, never interpreted as an empty inventory. *)
let verilog_instances ~top raw =
  let n=String.length raw in
  let white=function ' '| '\t'|'\r'|'\n' -> true | _ -> false in
  let rec lex i acc = if i=n then List.rev acc else
    if white raw.[i] then lex (i+1) acc
    else if i+1<n && String.sub raw i 2="//" then (match String.index_from_opt raw (i+2) '\n' with None -> List.rev acc | Some j -> lex (j+1) acc)
    else if i+1<n && (String.sub raw i 2="/*" || String.sub raw i 2="(*") then (
      let close=if raw.[i+1]='*' then "*/" else "*)" in
      let j=try Str.search_forward (Str.regexp_string close) raw (i+2) with Not_found -> invalid_arg "unterminated netlist comment/attribute" in lex (j+2) acc)
    else if raw.[i]='\\' then (let j=ref (i+1) in while !j<n && not (white raw.[!j]) do incr j done;
      if !j=n then invalid_arg "unterminated escaped identifier";lex !j (String.sub raw (i+1) (!j-i-1)::acc))
    else if String.contains "();,[]{}:#=" raw.[i] then lex (i+1) (String.make 1 raw.[i]::acc)
    else (let j=ref (i+1) in while !j<n && not (white raw.[!j]) && not (String.contains "();,[]{}:#=" raw.[!j]) do incr j done;
      lex !j (String.sub raw i (!j-i)::acc)) in
  let rec statement acc = function [] -> invalid_arg "truncated netlist statement" | ";"::xs -> List.rev acc,xs | x::xs -> statement (x::acc) xs in
  let rec ports stack = function
    | [] -> invalid_arg "truncated instance/header port list"
    | ";"::_ -> invalid_arg "semicolon inside instance/header port list"
    | ")"::xs when stack=[] -> xs
    | ("("|"["|"{" as token)::xs -> ports ((match token with "(" -> ")" | "[" -> "]" | _ -> "}")::stack) xs
    | (")"|"]"|"}" as token)::xs -> (match stack with
      | close::rest when close=token -> ports rest xs
      | _ -> invalid_arg "unbalanced instance/header delimiters")
    | _::xs -> ports stack xs in
  let rec instances typ name acc xs =
    let xs=ports [] xs in
    let acc=(name,typ)::acc in
    match xs with
    | ";"::xs -> acc,xs
    | ","::name::"("::xs -> instances typ name acc xs
    | _ -> invalid_arg "unsupported trailing tokens in instance statement" in
  let rec body acc = function
    | ["endmodule"] -> List.rev acc
    | "endmodule"::_ -> invalid_arg "multiple modules/trailing netlist tokens unsupported"
    | [] -> invalid_arg "missing endmodule"
    | ("wire"|"input"|"output"|"inout"|"assign")::xs -> let _,xs=statement [] xs in body acc xs
    | typ::name::"("::xs ->
      if List.mem typ ["always";"initial";"generate";"if";"for"] then invalid_arg "behavioral netlist unsupported";
      let acc,xs=instances typ name acc xs in body acc xs
    | _ -> invalid_arg "unsupported gate netlist statement" in
  match lex 0 [] with "module"::name::"("::xs when name=top -> (match ports [] xs with
    | ";"::xs -> body [] xs | _ -> invalid_arg "unsupported module header tokens")
    | _ -> invalid_arg "netlist top/header mismatch"

(* A checkpoint NL identifies cells, not their areas or optimized flop owners.
   Reconcile the declared macro population jointly. Unrelated cells are outside
   this observation, not implicitly classified as verified standard cells. *)
let checkpoint_macro_rows ~top ~inventory ~netlist ~refs =
  let cells = verilog_instances ~top netlist and exp = expected inventory in
  unique "netlist instance" (List.map fst cells);
  let types = List.map (fun r -> text (get r "module")) exp in
  let names = List.concat_map (fun r -> List.map (fun i -> text (get i "instance"))
    (list (get r "instances"))) exp in
  let relevant = List.filter (fun (n,t) -> List.mem n names || List.mem t types) cells in
  let reconciled = reconcile ~expected:exp ~instances:(Some relevant) ~known_standard_cells:[] ~reason:None in
  List.map (fun row -> obj (fields row @ ["stage",s "synthesis";
    "producer",s "verified checkpoint native state.nl"; "evidence_refs",arr refs]))
    (list (get reconciled "resources"))

let measurement ~stage ~refs ~scope ~unit_ ~definition value reason = obj ["value",value;"unit",s unit_;"definition",s definition;
  "stage",s stage;"scope",s scope;"evidence_refs",arr refs;"unavailable_reason",(if value=`Null then s reason else `Null)]
(* Liberty cell area is a direct cell attribute. Comments, nested pin/table
   attributes and quoted text cannot supply it; duplicate cells/areas are errors. *)
let liberty_cell_area ~name raw =
  let n=String.length raw in
  let white=function ' '| '\t'|'\r'|'\n' -> true | _ -> false in
  let rec lex i acc = if i=n then Array.of_list (List.rev acc) else
    if white raw.[i] then lex (i+1) acc
    else if i+1<n && String.sub raw i 2="/*" then (
      let j=try Str.search_forward (Str.regexp_string "*/") raw (i+2) with Not_found -> invalid_arg "unterminated Liberty comment" in lex (j+2) acc)
    else if i+1<n && String.sub raw i 2="//" then (
      match String.index_from_opt raw (i+2) '\n' with None -> lex n acc | Some j -> lex (j+1) acc)
    else if raw.[i]='"' then (
      let rec quoted j = if j>=n then invalid_arg "unterminated Liberty string" else
        if raw.[j]='\\' then quoted (j+2) else if raw.[j]='"' then j+1 else quoted (j+1) in
      let j=quoted (i+1) in lex j (String.sub raw i (j-i)::acc))
    else if String.contains "(){}:;," raw.[i] then lex (i+1) (String.make 1 raw.[i]::acc)
    else (let j=ref (i+1) in while !j<n && not (white raw.[!j]) && not (String.contains "(){}:;,\"" raw.[!j]) do incr j done;
      lex !j (String.sub raw i (!j-i)::acc)) in
  let tokens=lex 0 [] in
  let depth=ref 0 and cell_depth=ref None and areas=ref [] and cells=ref 0 in
  Array.iteri (fun i token ->
    if token="cell" && i+4<Array.length tokens && tokens.(i+1)="(" &&
       (tokens.(i+2)=name || tokens.(i+2)="\""^name^"\"") && tokens.(i+3)=")" && tokens.(i+4)="{" then (
      incr cells;cell_depth:=Some (!depth+1));
    (if token="area" && !cell_depth=Some !depth then (
      if i+3>=Array.length tokens || tokens.(i+1)<>":" || tokens.(i+3)<>";" then invalid_arg "malformed Liberty cell area";
      let f=try float_of_string tokens.(i+2) with Failure _ -> invalid_arg "invalid Liberty cell area" in
      if not (Float.is_finite f) || f<0. then invalid_arg "nonfinite/negative Liberty cell area";
      areas:=f::!areas));
    if token="{" then incr depth else if token="}" then (
      if !cell_depth=Some !depth then cell_depth:=None;
      decr depth;if !depth<0 then invalid_arg "unbalanced Liberty braces")) tokens;
  if !depth<>0 then invalid_arg "truncated Liberty groups";
  match !cells,!areas with 1,[area] -> area | _ -> invalid_arg "missing/duplicate Liberty macro cell or direct area"
let run ?context ?(schema_version=2) ~run_dir () =
  if String.trim run_dir="" then invalid_arg "resource inspection requires a nonempty run directory";
  let r={root=Unix.realpath run_dir;refs=[];bytes=0} in
  let record,run_ref=match context with
    | Some c -> let c=normalize c in
      if file (Filename.concat r.root "run.json") then (
        let disk,ref_=json r "run.json" in
        c,(if E.record_bytes disk=E.record_bytes c then ref_ else unavailable "explicit collection context differs from run.json; context digest retained"))
      else c,unavailable "in-memory execution context; no coarse run.json (context digest retained)"
    | None -> json r "run.json" in
  let manifest_path=require_path r manifest_paths in
  let manifest,manifest_ref=json r manifest_path in
  (match Hardcaml_asic.Elaboration.Bundle.verify_manifest_identity manifest with Ok () -> () | Error _ -> invalid_arg "bundle manifest payload identity or resource snapshot invalid");
  if get record "build_identity"<>get manifest "identity" then invalid_arg "run/build manifest identity mismatch";
  (match get record "manifest_sha256" with `Null when context<>None -> () | digest ->
    if digest<>get manifest_ref "sha256" then invalid_arg "run manifest digest mismatch");
  let run_id=text (get record "run_id") and build=get record "build_identity" in
  if not (List.mem (get record "requested_stage") [s "synthesis";s "full"]) then invalid_arg "unknown producing run stage";
  let coverage,rows=inventory manifest in
  unique "resource ID" (List.map (fun row -> text (get row "resource_id")) rows);
  let exp=expected rows in
  let top=text (get manifest "top_module") in
  if Filename.basename top<>top then invalid_arg "unsafe top module";
  let flow=match get (get record "outputs") "flow" with
    | `String p when Filename.is_relative p && not (List.mem ".." (String.split_on_char '/' p)) -> p
    | `String p when String.starts_with ~prefix:(r.root^"/") p -> String.sub p (String.length r.root+1) (String.length p-String.length r.root-1)
    | _ -> invalid_arg "producing flow path is missing or outside the selected run" in
  let synth=flow^"/06-yosys-synthesis" in
  (* Root aliases belong only to the explicit retained snapshot layouts, never
     to an unrelated/missing producing flow in an otherwise staged live run. *)
  let snapshot_layout=manifest_path<>"project/manifest.json" && flow="project/runs/asic" in
  let paths primary aliases=primary @ if snapshot_layout then aliases else [] in
  let synthesis_complete=List.mem (get record "completed_stage") [s "synthesis";s "full"] in
  let cell_areas=ref [] in
  let macro_types=List.map (fun x -> text (get x "module")) exp in
  let standard t = List.mem_assoc t !cell_areas && not (List.mem t macro_types) in
  let observe stage paths parser =
    let before=List.length r.refs in
    let data,reason=match first r paths with None -> None,Some "retained instance evidence unavailable" | Some p ->
      (try Some (parser p),None with Invalid_argument e -> None,Some e) in
    let refs=List.filteri (fun i _ -> i>=before) r.refs in
    let known=Option.value data ~default:[] |> List.filter_map (fun (_,t) -> if standard t then Some t else None) |> List.sort_uniq String.compare in
    let result=reconcile ~expected:exp ~instances:data ~known_standard_cells:known ~reason in
    let reconciled=list (get result "resources") in
    let resources=List.map (fun row ->
      let matched=List.find_opt (fun x -> get x "resource_id"=get row "resource_id") reconciled in
      let extra=match matched with Some x -> fields x |> List.filter (fun (k,_) -> k<>"resource_id") | None ->
        ["expected",unavailable "flop implementation has no retained resource-instance mapping";
         "realized",unavailable "optimization loses per-resource flop ownership";"status",s "unresolved";"reason",s "flop attribution unavailable; absence is not optimized-away evidence"] in
      let attribution=match matched with Some x when get x "status"=s "matched" -> obj ["status",s "available";"reason",s "exact macro instance identity/count observed; resource cost is separately scoped"]
        | _ -> unavailable "per-resource optimized flop attribution or unresolved macro ownership unavailable" in
      obj (fields row @ extra @ ["attribution",attribution;
        "area",measurement ~stage ~refs ~scope:"per-resource area" ~unit_:"um^2" ~definition:"resource area attribution" `Null "no resource-specific area evidence retained";
        "timing_links",unavailable "no resource-linked path evidence retained";"electrical_links",unavailable "global checks do not establish per-resource attribution";"evidence_refs",arr refs])) rows in
    obj ["stage",s stage;"producer",s (if stage="synthesis" then "Yosys.Synthesis" else "retained final implementation netlist");
      "producer_origin",(if get record "evidence_origin"=`Null then obj ["kind",s "coarse_run_retained_artifacts";"stage",s stage]
        else obj ["kind",s "mapped_checkpoint_evidence";"stage",s stage;"recorded_origin",get record "evidence_origin";
          "scope",s "checkpoint boundary is not a newly measured producer; original selected checkpoint identities retained"]);
      "run_id",s run_id;"build_identity",build;"resources",arr resources;"unexpected_instances",get result "unexpected_instances";
      "inventory_coverage",s "declared macro modules and supported blackbox leaf standard-cell modules with finite library area attributes; non-leaf/unsupported hierarchy unresolved";
      "cell_type_counts",(match data with None -> unavailable "instance inventory unavailable" | Some cells ->
        let types=List.fold_left (fun types (_,typ) -> (typ,1+Option.value (List.assoc_opt typ types) ~default:0)::List.remove_assoc typ types) [] cells |> List.sort compare in
        obj ["status",s "observed";"scope",s "whole-design, no logical flop-resource attribution";"types",arr (List.map (fun (typ,n) -> obj ["module",s typ;"count",`Int n;
          "classification",s (if List.exists (fun e -> get e "module"=s typ) exp then "declared_macro" else if standard typ then "standard_cell" else "unknown")]) types)]);
      "measurements",obj [];"status",get result "status";"reason",get result "reason";"evidence_refs",arr refs],data in
  let synthesis,data=observe "synthesis" (paths [synth^"/"^top^".nl.v.json"] ["mapped.json"]) (fun p ->
    if not synthesis_complete then invalid_arg "synthesis completion/producer boundary not established by run record";
    let mapped,_=json r p in
    ignore (text (get mapped "creator"));
    let leaf m =
      let blackbox=match get (get m "attributes") "blackbox" with
        | `Int 1 -> true
        | `String bits when String.length bits>0 -> bits.[String.length bits-1]='1' &&
          String.for_all ((=) '0') (String.sub bits 0 (String.length bits-1))
        | _ -> false in
      blackbox && List.for_all (fun key -> match get m key with `Null|`Assoc [] -> true | _ -> false) ["cells";"processes";"memories"] in
    cell_areas:=fields (get mapped "modules") |> List.filter_map (fun (typ,m) ->
      let family=List.exists (fun prefix -> String.starts_with ~prefix typ && String.length typ>String.length prefix &&
        String.for_all (function 'a'..'z'|'0'..'9'|'_' -> true | _ -> false)
          (String.sub typ (String.length prefix) (String.length typ-String.length prefix))) ["sg13g2_";"sg13cmos5l_";"sky130_fd_sc_"] in
      if not family || List.mem typ macro_types || not (leaf m) then None else match get (get m "attributes") "area" with
      | `String value -> let f=try float_of_string value with Failure _ -> invalid_arg "invalid mapped library area" in
        if not (Float.is_finite f) || f<0. then invalid_arg "nonfinite mapped library area";Some (typ,f)
      | `Null -> None | v -> Some (typ,numeric "mapped library area" v));
    let design=get (get mapped "modules") top in
    (match get design "processes" with `Null|`Assoc [] -> () | _ -> invalid_arg "mapped JSON still has processes");
    fields (get design "cells") |> List.map (fun (name,c) ->
      let typ=text (get c "type") in
      if not (leaf (get (get mapped "modules") typ)) then invalid_arg ("unsupported non-leaf/non-library mapped module: "^typ);
      name,typ)) in
  let synthesis=ref synthesis in
  let measure=ref [] in
  let add name stage refs scope unit_ definition value reason = measure:= !measure@[name,measurement ~stage ~refs ~scope ~unit_ ~definition value reason] in
  let macro_count=Option.map (fun xs -> List.length (List.filter (fun (_,t) -> List.mem t macro_types) xs)) data in
  let count_measure name v definition = add name "synthesis" (list (get !synthesis "evidence_refs")) "whole-design mapped netlist" "count" definition
    (match v with None -> `Null | Some n -> `Int n) "instance inventory unavailable" in
  count_measure "total_mapped_cells" (Option.map List.length data) "all mapped cells, including macros";
  count_measure "macro_count" macro_count "instances of declared macro modules";
  count_measure "standard_cell_count" (Option.bind data (fun xs -> if List.for_all (fun (_,t) -> standard t || List.mem t macro_types) xs then Some (List.length (List.filter (fun (_,t) -> standard t) xs)) else None)) "classified standard cells; excludes macros";
  let stats=if not synthesis_complete then None else match first r (paths [synth^"/reports/stat.json"] ["stat.json"]) with None -> None | Some p ->
    (try let stat,ref_=json r p in
      let design=get stat "design" in
      let types,_=Yosys_stat_evidence.validate_counts ~mapped_count:(Option.map (fun xs -> Z.of_int (List.length xs)) data) design in
      Option.iter (fun xs -> let actual=List.fold_left (fun acc (_,t) -> let n=1+Option.value (List.assoc_opt t acc) ~default:0 in (t,n)::List.remove_assoc t acc) [] xs in
        if List.sort compare actual<>List.sort compare (List.map (fun (t,n) -> t,Z.to_int n) types) then invalid_arg "mapped JSON/stat type counts disagree") data;
      Some (stat,ref_) with Invalid_argument e -> synthesis:=obj (List.remove_assoc "status" (fields !synthesis) @ ["status",s "fail";"measurement_error",s e]);None) in
  let area=Option.bind stats (fun (stat,_) ->
    let reported=numeric "synthesis area" (get (get stat "design") "area") in
    Option.bind data (fun cells ->
      if not (List.for_all (fun (_,t) -> standard t || List.mem t macro_types) cells) then None else
      let sum=List.fold_left (fun sum (_,t) -> if standard t then sum+.List.assoc t !cell_areas else sum) 0. cells in
      if abs_float (sum-.reported)>1e-5 then None else Some reported)) in
  let stat_refs=match stats with None -> [] | Some (_,ref_) -> [ref_] in
  let area_value=match area with None -> `Null | Some f -> `Float f in
  let area_refs=stat_refs@list (get !synthesis "evidence_refs") in
  add "synthesis_standard_cell_area" "synthesis" area_refs "stat area cross-checked against sum of standard-cell mapped library area attributes, excluding macros" "um^2" "synthesis standard-cell area, not placed area" area_value "stat/instance library area inclusion cross-check unavailable or inconsistent";
  let sequential=Option.map (fun (stat,_) -> get (get stat "design") "sequential_area") stats |> Option.value ~default:`Null in
  add "whole_design_sequential_area" "synthesis" stat_refs "whole-design library-known sequential cells; no logical RAM/flop-resource ownership; excludes macro library area" "um^2"
    "producer sequential cell area, not SRAM implementation flop attribution" sequential "producer sequential area unavailable";
  add "sequential_cell_count" "synthesis" (list (get !synthesis "evidence_refs")) "whole-design; typed cell counts retained separately" "count"
    "sequential cell count requires retained sequential classification" `Null "mapped JSON does not retain sequential cell classification; do not infer logical resource ownership";
  let macro_area_error=ref "retained bound macro Liberty unavailable" in
  let resource_areas=ref [] in
  let macro_area=try
    let values=List.map (fun e ->
      let typ=text (get e "module") in
      let config=get manifest "flow_configuration" in
      let libs=get (get (get config "MACROS") typ) "lib" in
      let corner=text (get config "DEFAULT_CORNER") in
      let candidates=list (get libs corner) |> List.map text in
      let reference=match candidates with [p] -> p | _ -> invalid_arg "ambiguous macro Liberty corner binding" in
      let lib=Filename.basename reference in
      let p=require_path r ["models/"^lib;"baseline-inputs/"^lib;"bundle/"^lib] in
      let raw,ref_=read r p "liberty" in
      let preflight=require_path r ["preflight.json";"run-preflight.json";"public-preflight.json"] in
      let preflight,pref=json r preflight in
      let bindings=list (get preflight "references") |> List.filter (fun b -> get b "reference"=s reference) in
      (match bindings with [b] when get b "sha256"=get ref_ "sha256" -> () | _ -> invalid_arg "macro Liberty bytes not bound by retained preflight reference");
      let f=liberty_cell_area ~name:typ raw in
      let value=f *. float_of_int (List.length (list (get e "instances"))) in
      resource_areas:=(get e "resource_id",(value,[ref_;pref]))::!resource_areas;
      value,[ref_;pref]) exp in
    Some (List.fold_left (fun n (v,_) -> n+.v) 0. values,List.concat_map snd values)
    with Invalid_argument e | Failure e -> macro_area_error:=e;None
      | Not_found -> macro_area_error:="missing macro Liberty cell";None in
  let ma,refs=match macro_area with Some (f,refs) -> `Float f,refs | None -> `Null,[] in
  add "macro_library_area" "synthesis" refs "declared macro instances times default-corner Liberty cell area" "um^2" "macro library area, not physical footprint" ma !macro_area_error;
  let combined=match area,macro_area,stats with Some a,Some (m,_),Some _ when get !synthesis "status"=s "pass" -> `Float (a+.m)
    | _ -> `Null in
  add "combined_cell_area_estimate" "synthesis" (area_refs@refs) "synthesis standard-cell area plus macro Liberty area; excludes placement/routing/physical-only cells" "um^2" "combined cell-area estimate, not placed/routed, core or die area" combined "area inclusion/binding evidence unavailable";
  let resources=list (get !synthesis "resources") |> List.map (fun row ->
    let value,refs,reason=match List.assoc_opt (get row "resource_id") !resource_areas with
      | Some (value,refs) when get row "status"=s "matched" -> `Float value,refs,""
      | Some _ -> `Null,[],"macro identity/count not matched; selected library cost is not realized resource area"
      | _ -> `Null,[],(if get (get row "selected_implementation") "kind"=s "flops" then "optimized flop-resource mapping unavailable" else !macro_area_error) in
    let is_macro=get (get row "selected_implementation") "kind"=s "macro" in
    obj (List.remove_assoc "area" (fields row)@["area",measurement ~stage:"synthesis" ~refs
      ~scope:(if is_macro then "exact observed macro instance count times default-corner Liberty cell area" else "logical resource; synthesis ownership mapping unavailable") ~unit_:"um^2"
      ~definition:(if is_macro then "resource macro library-area estimate; not physical footprint or placed cost" else "per-resource flop area attribution") value reason])) in
  synthesis:=obj (List.remove_assoc "resources" (List.remove_assoc "measurements" (fields !synthesis)) @ ["measurements",obj !measure;"resources",arr resources]);
  let observations=ref [!synthesis] in
  let reader : Resource_physical_evidence.reader =
    {read=(fun path format -> read r path format);exists=(fun path ->
       if not(Filename.is_relative path) || List.exists(fun p -> p=".." || p="." || p="") (String.split_on_char '/' path) then false
       else let path=Filename.concat r.root path in
         file path && String.starts_with ~prefix:(r.root^"/") (Unix.realpath path))} in
  if get record "requested_stage"=s "full" then (
    let verified_checks=ref None in
    let checker_ref=ref `Null in
    let final_netlist=ref `Null in
    let final_flow=ref flow in
    let physical,physical_data=observe "final_physical" (paths [flow^"/final/nl/"^top^".nl.v"] ["flow/final/nl/"^top^".nl.v"]) (fun p ->
      if get record "completed_stage"<>s "full" then invalid_arg "final physical stage not reached";
      final_flow:=(if String.starts_with ~prefix:"flow/" p then "flow" else flow);
      if get record "physical_checks_sha256"<>`Null || get (get manifest "macro_contract") "execution"=s "fresh_full" then (
        let checks,ref_=json r "physical-checks.json" in
        if get ref_ "sha256"<>get record "physical_checks_sha256" then invalid_arg "physical checker record digest mismatch";
        if get checks "schema_version"<>`Int 1 || get checks "status"<>s "pass" ||
           get checks "checking_profile"<>s "cmos5l_real_gds_v1" || get checks "lvs_scope"<>s "abstract_macro_boundary_with_schematic_cdl" then invalid_arg "unsupported/failed retained physical checks";
        let actual_flow=if String.starts_with ~prefix:"flow/" p then "flow" else flow in
        let artifacts=fields (get checks "artifacts") in
        if not (List.mem_assoc ("final/nl/"^top^".nl.v") artifacts) then invalid_arg "physical checker does not cover final netlist";
        List.iter (fun (path,digest) ->
          if not (Filename.is_relative path) || List.mem ".." (String.split_on_char '/' path) then invalid_arg "unsafe physical checker artifact";
          let ref_=hash_source r (actual_flow^"/"^path) "physical_checker_artifact" in
          if get ref_ "sha256"<>digest then invalid_arg ("physical artifact hash mismatch: "^path)) artifacts;
        let gds=require_path r [actual_flow^"/final/gds/"^top^".gds";"final.gds"] in
         if get (hash_source r gds "gds") "sha256"<>get checks "gds_sha256" then invalid_arg "physical final GDS digest mismatch";
         verified_checks:=Some checks;checker_ref:=ref_);
       let raw,ref_=read r p "verilog_gate_netlist" in
       let cells=verilog_instances ~top raw in final_netlist:=ref_;cells) in
    let measurements=ref [] in
    List.iter (fun (name,selector,definition) ->
      let value=match physical_data with None -> `Null | Some cells ->
        if name="standard_cell_count" && not (List.for_all (fun (_,typ) -> standard typ || List.mem typ macro_types) cells) then `Null
        else `Int (List.length (List.filter selector cells)) in
      measurements:= !measurements@[name,measurement ~stage:"final_physical" ~refs:(list (get physical "evidence_refs"))
        ~scope:"final implementation netlist; includes filler/decap/tap/tie cells present in this netlist, excludes omitted physical-only cells; not placement database instance count"
        ~unit_:"count" ~definition value "complete classified final instance inventory unavailable"])
      ["total_physical_netlist_cells",(fun _ -> true),"all final implementation netlist instances, including macros";
       "macro_count",(fun (_,typ) -> List.mem typ macro_types),"declared macro module instances in final implementation netlist";
       "standard_cell_count",(fun (_,typ) -> standard typ),"classified standard-cell instances in final implementation netlist"];
    (match first r (paths [flow^"/final/metrics.json"] ["metrics.json"]) with None -> () | Some p ->
      let metrics,ref_=json r p in
      List.iter (fun (name,key,unit_,definition) -> let raw=get metrics key in
        let value=if raw=`Null then `Null else `Float (numeric key raw) in
        measurements:= !measurements@[name,measurement ~stage:"final_physical" ~refs:[ref_] ~scope:"native final metrics; standard cells exclude macros; physical-only inclusion is producer-defined" ~unit_ ~definition value "native final metric absent"])
        ["final_standard_cell_area","design__instance__area__stdcell","um^2","final physical standard-cell area, distinct from synthesis";
         "standard_cell_utilization","design__instance__utilization__stdcell","ratio","producer standard-cell utilization; denominator/excluded regions unavailable; not whole-design occupancy"]);
     let physical=obj (List.remove_assoc "measurements" (fields physical)@["measurements",obj !measurements]) in
     let physical=if schema_version=1 then physical else (
       let actual_flow= !final_flow in
       let extra,geometries=try Resource_physical_evidence.collect ~reader ~flow:actual_flow ~snapshot:snapshot_layout
         ~manifest
         ~resources:(list(get physical "resources")) ~instances:physical_data ~checks:!verified_checks
         ~checker_ref:!checker_ref ~final_netlist:!final_netlist
         with Invalid_argument e | Failure e ->
           obj["status",s "invalid";"reason",s e],[] in
       let resources=List.map(fun row ->
         let geometry=Option.value (List.assoc_opt (text(get row "resource_id")) geometries)
           ~default:(unavailable "physical geometry unavailable; see observation diagnostics") in
         let area=if get geometry "status"=s "available" then measurement ~stage:"final_physical"
           ~refs:(list(get geometry "evidence_refs")) ~scope:"exact reconciled macro footprint; excludes halo, bridges, routing and surrounding cells"
           ~unit_:"um^2" ~definition:"physical macro footprint, not Liberty estimate or integration envelope"
           (get geometry "area_um2") "" else get row "area" in
         obj(List.remove_assoc "area" (fields row)@["area",area;"geometry",geometry])) (list(get physical "resources")) in
       let timing=if physical_data=None then Ok {Resource_timing_evidence.resources=[];diagnostics=["final netlist identity unavailable"]}
         else Resource_timing_evidence.collect ~read:reader.read ~exists:reader.exists ~flow:actual_flow
           ~context:record ~manifest ~resources ~final_netlist:!final_netlist in
       let resources=match timing with Error _ -> resources | Ok result ->
         List.map(fun row -> match List.find_opt(fun (links:Resource_timing_evidence.links) -> get row "resource_id"=s links.resource_id) result.resources with
           | None -> row | Some links -> obj(List.remove_assoc "electrical_links" (List.remove_assoc "timing_links" (fields row)) @
             ["timing_links",links.timing_links;"electrical_links",links.electrical_links])) resources in
       let diagnostics=match timing with Ok result -> result.diagnostics | Error e -> [e] in
       let fs=List.remove_assoc "resources" (fields physical) in
       let fs=if get extra "status"=s "invalid" then List.remove_assoc "status" fs@["status",s "fail";"measurement_error",get extra "reason"] else fs in
       let fs=match timing with Ok _ -> fs | Error e -> List.remove_assoc "measurement_error" (List.remove_assoc "status" fs)@["status",s "fail";"measurement_error",s e] in
       obj(fs@["resources",arr resources;"physical",extra;"timing_diagnostics",arr(List.map s diagnostics)])) in
     observations:= !observations@[physical]);
   if schema_version=2 then observations:=List.map(fun o ->
     if get o "stage"=s "final_physical" then o else
       obj(List.remove_assoc "resources" (fields o)@["resources",arr(List.map(fun row ->
         obj(fields row@["geometry",unavailable "synthesis has no final physical geometry"])) (list(get o "resources")))])) !observations;
  List.iter (fun ref_ -> let p=Filename.concat r.root (text (get ref_ "path")) in
    match Checkpoint.file_record p with Ok f when s f.sha256=get ref_ "sha256" -> () | _ -> invalid_arg "resource evidence changed after capture") r.refs;
   obj ["contract",s "hardcaml-asic.resource-evidence";"schema_version",`Int schema_version;"coverage",s coverage;
    "resource_inventory",arr rows;
    "provenance",obj ["run_id",s run_id;"build_identity",build;"manifest",manifest_ref;"run",run_ref;
      "requested_stage",get record "requested_stage";"completed_stage",get record "completed_stage";
      "context_sha256",s (Identity_json.sha256_bytes (E.record_bytes record));
      "evidence_origin",get record "evidence_origin";
      "preparation_link",unavailable "no verified preparation-to-bundle linkage recorded";"source_authentication",s "unavailable; evidence integrity only"];
    "observations",arr !observations;"check_outcomes",arr (List.map (fun o -> obj ["stage",get o "stage";"status",get o "status";"required",`Bool (exp<>[] || list (get o "unexpected_instances")<>[])]) !observations);
    "evidence_refs",arr r.refs]
let protect f = try Ok (f ()) with
  | Invalid_argument e | Failure e | Sys_error e -> Error e
  | Not_found -> Error "unsupported or incomplete resource evidence"
  | End_of_file -> Error "truncated resource evidence"
  | Unix.Unix_error (e,f,p) -> Error (f^" "^p^": "^Unix.error_message e)
let evaluate evidence =
    let evidence=normalize evidence in
    (match Identity_json.parse (E.record_bytes evidence) with Ok _ -> () | Error _ -> invalid_arg "resource evidence contains non-strict JSON values");
    let equal a b = E.record_bytes a=E.record_bytes b in
    let exact j keys = let actual=List.map fst (fields j) in unique "evidence field" actual;
      if List.sort String.compare actual<>List.sort String.compare keys then invalid_arg "malformed resource evidence object fields" in
    exact evidence ["contract";"schema_version";"coverage";"resource_inventory";"provenance";"observations";"check_outcomes";"evidence_refs"];
     let schema_version=get evidence "schema_version" in
     if get evidence "contract"<>s "hardcaml-asic.resource-evidence" || not(List.mem schema_version [`Int 1;`Int 2]) then invalid_arg "unsupported resource evidence schema";
     let enriched=schema_version=`Int 2 in
    if not (List.mem (get evidence "coverage") [s "resource_snapshot";s "legacy_resources_macro_contract"]) then invalid_arg "unknown resource evidence coverage";
    let inventory_keys=["resource_id";"requested_contract";"selected_implementation";"instances";"selection";"requirement";"policy";"inventory_record"] in
    let inventory_rows=list (get evidence "resource_inventory") in
    List.iter (fun row -> exact row inventory_keys;
      ignore (text (get row "resource_id"));
      let impl=get row "selected_implementation" in
      match text (get impl "kind") with
      | "flops" -> exact impl ["kind"];if get row "instances"<>arr [] then invalid_arg "flop inventory has macro instances"
      | "macro" -> exact impl ["kind";"name"];ignore (text (get impl "name"));
        if list (get row "instances")=[] then invalid_arg "macro inventory lacks required instances";
        List.iter (fun i -> exact i ["module";"instance"];ignore (text (get i "instance"));
          if get i "module"<>get impl "name" then invalid_arg "inventory instance type disagrees with selection") (list (get row "instances"))
      | _ -> invalid_arg "unknown inventory implementation") inventory_rows;
    let inventory_ids=List.map (fun r -> text (get r "resource_id")) inventory_rows in
    unique "inventory resource ID" inventory_ids;
    (* The rows retain the original records. Re-project them with the same
       adapter used at collection so duplicated selection fields cannot drift,
       even in an otherwise self-consistent set of observations. *)
    let records=arr (List.map (fun r -> get r "inventory_record") inventory_rows) in
    let macros=List.concat_map (fun r -> List.map (fun i ->
      obj ["resource",get r "resource_id";"module",get i "module";"instance",get i "instance"])
      (list (get r "instances"))) inventory_rows in
    let source=if get evidence "coverage"=s "resource_snapshot" then
      "resource_snapshot",obj ["schema_version",`Int 1;"project",s "inventory";"top",s "inventory";
        "scope",s "resolved";"mode",s "implementation";"resources",records]
      else "resources",records in
    let _,projected=inventory (obj [source;"macro_contract",obj ["schema_version",`Int 1;"macros",arr macros]]) in
    if not (List.for_all2 equal inventory_rows projected) then invalid_arg "inventory projection disagrees with recorded resources";
    let expected_inventory=expected inventory_rows in
    let macro_types=List.map (fun e -> text (get e "module")) expected_inventory |> List.sort_uniq String.compare in
    unique "inventory instance identity" (List.concat_map (fun e -> List.map (fun i -> text (get i "instance")) (list (get e "instances"))) expected_inventory);
    let provenance=get evidence "provenance" in
    exact provenance ["run_id";"build_identity";"manifest";"run";"requested_stage";"completed_stage";"context_sha256";"evidence_origin";"preparation_link";"source_authentication"];
    let link=get provenance "preparation_link" in exact link ["status";"reason"];
    if get link "status"<>s "unavailable" then invalid_arg "unsupported preparation linkage claim";
    ignore (text (get link "reason"));
    ignore (text (get provenance "run_id"));ignore (text (get provenance "build_identity"));
    let refs=list (get evidence "evidence_refs") in
    List.iter (fun r ->
      exact r ["path";"sha256";"format";"scope"];
      let path=text (get r "path") and digest=text (get r "sha256") in
      if not (Filename.is_relative path) || List.exists (fun c -> c=".." || c="." || c="") (String.split_on_char '/' path) then invalid_arg "unsafe resource evidence reference";
      if String.length digest<>64 || not (String.for_all (function '0'..'9'|'a'..'f' -> true | _ -> false) digest) then invalid_arg "malformed evidence digest";
      ignore (text (get r "format"));ignore (text (get r "scope"))) refs;
    unique "evidence path" (List.map (fun r -> text (get r "path")) refs);
    if not (List.mem (get provenance "manifest") refs) then invalid_arg "manifest provenance is not covered by evidence references";
    (match get (get provenance "run") "status" with
     | `String "unavailable" -> ignore (text (get (get provenance "run") "reason"))
     | _ -> if not (List.mem (get provenance "run") refs) then invalid_arg "run provenance is not covered by evidence references");
    let context_digest=text (get provenance "context_sha256") in
    if String.length context_digest<>64 || not (String.for_all (function '0'..'9'|'a'..'f' -> true | _ -> false) context_digest) then invalid_arg "malformed context digest";
    let observations=list (get evidence "observations") in
    let stages=List.map (fun o -> text (get o "stage")) observations in
    unique "observation stage" stages;
    let expected_stages=match get provenance "requested_stage" with `String "synthesis" -> ["synthesis"] | `String "full" -> ["synthesis";"final_physical"]
      | _ -> invalid_arg "unsupported producing run stage" in
    if stages<>expected_stages then invalid_arg "resource observation stages disagree with producing run";
    List.iter (fun o ->
       exact o (["stage";"producer";"producer_origin";"run_id";"build_identity";"resources";"unexpected_instances";"inventory_coverage";
         "cell_type_counts";"measurements";"status";"reason";"evidence_refs"] @ (if enriched && get o "stage"=s "final_physical" then ["physical";"timing_diagnostics"] else []) @ (if get o "measurement_error"=`Null then [] else ["measurement_error"]));
       if enriched && get o "stage"=s "final_physical" then (
         List.iter(fun v -> ignore(text v)) (list(get o "timing_diagnostics"));
         Resource_physical_evidence.validate ~refs (get o "physical");
         if get (get o "physical") "status"=s "invalid" && get o "status"<>s "fail" then invalid_arg "invalid physical evidence has passing verdict");
      if get o "run_id"<>get provenance "run_id" || get o "build_identity"<>get provenance "build_identity" then invalid_arg "resource observation provenance mismatch";
      if get o "stage"=s "synthesis" && get o "status"=s "pass" && not (List.mem (get provenance "completed_stage") [s "synthesis";s "full"]) then invalid_arg "passing synthesis resource check before synthesis completion";
      if get o "stage"=s "final_physical" && get o "status"=s "pass" && get provenance "completed_stage"<>s "full" then invalid_arg "passing physical resource check before physical completion";
      List.iter (fun ref_ -> if not (List.mem ref_ refs) then invalid_arg "unclosed resource evidence reference") (list (get o "evidence_refs"));
      let check_measurement m =
        exact m ["value";"unit";"definition";"stage";"scope";"evidence_refs";"unavailable_reason"];
        if get m "stage"<>get o "stage" then invalid_arg "measurement stage mismatch";
        (match get m "value" with `Null -> ignore (text (get m "unavailable_reason")) | v -> ignore (numeric "resource measurement" v);
          if get m "unavailable_reason"<>`Null then invalid_arg "available measurement has unavailable reason");
        List.iter (fun ref_ -> if not (List.mem ref_ refs) then invalid_arg "unclosed measurement reference") (list (get m "evidence_refs")) in
      List.iter (fun (_,m) -> check_measurement m) (fields (get o "measurements"));
      let observed_types=match get (get o "cell_type_counts") "status" with
       | `String "unavailable" -> ignore (text (get (get o "cell_type_counts") "reason"));None
       | `String "observed" ->
         let types=list (get (get o "cell_type_counts") "types") in
         unique "observed cell type" (List.map (fun t -> text (get t "module")) types);
         List.iter (fun t -> exact t ["module";"count";"classification"];
           let n=numeric "cell type count" (get t "count") in
           if floor n<>n || n=0. then invalid_arg "invalid observed type count";
           if not (List.mem (get t "classification") [s "declared_macro";s "standard_cell";s "unknown"]) then invalid_arg "unknown cell classification";
           if (get t "classification"=s "declared_macro")<>List.mem (text (get t "module")) macro_types then invalid_arg "cell classification disagrees with resource inventory") types;
         Some types
       | _ -> invalid_arg "malformed cell type inventory" in
      let count_keys=[(if get o "stage"=s "synthesis" then "total_mapped_cells" else "total_physical_netlist_cells");"macro_count";"standard_cell_count"] in
      List.iter (fun key -> let m=get (get o "measurements") key in
        check_measurement m;
        let expected_value=match observed_types with None -> `Null | Some types ->
          if key="standard_cell_count" && List.exists (fun t -> get t "classification"=s "unknown") types then `Null else
          let selected=List.filter (fun t -> if key="macro_count" then get t "classification"=s "declared_macro"
            else if key="standard_cell_count" then get t "classification"=s "standard_cell" else true) types in
          `Float (List.fold_left (fun n t -> n+.numeric "type count" (get t "count")) 0. selected) in
        let actual=get m "value" in
        if (match expected_value,actual with `Null,`Null -> false | `Float a,(`Int _|`Intlit _|`Float _ as b) -> a<>numeric key b | _ -> true)
        then invalid_arg ("instance count contradicts cell type inventory: "^key)) count_keys;
      let resources=list (get o "resources") in
      unique "observed resource ID" (List.map (fun row -> text (get row "resource_id")) resources);
      if List.sort String.compare (List.map (fun row -> text (get row "resource_id")) resources)<>List.sort String.compare inventory_ids then invalid_arg "observation omitted or added resource inventory rows";
      List.iter (fun row ->
         exact row (["resource_id";"requested_contract";"selected_implementation";"instances";"selection";"requirement";"policy";"inventory_record";
           "expected";"realized";"status";"reason";"area";"attribution";"timing_links";"electrical_links";"evidence_refs"] @ if enriched then ["geometry"] else []);
         if enriched then (
           if get o "stage"=s "final_physical" then (
             (match Resource_timing_evidence.validate_links ~references:refs ~resource:row
               ~timing_links:(get row "timing_links") ~electrical_links:(get row "electrical_links") with
             | Ok () -> () | Error e -> invalid_arg e);
             let links=get row "timing_links" in
             if get links "status"=s "observed" then (
               if get provenance "completed_stage"<>s "full" then invalid_arg "timing links before final completion";
               List.iter(fun path ->
               let p=get path "provenance" in
               if get p "run_id"<>get o "run_id" || get p "build_identity"<>get o "build_identity" then
                 invalid_arg "timing link observation provenance mismatch") (list(get links "paths"))))
           else List.iter(fun k -> let j=get row k in exact j ["status";"reason"];
             if get j "status"<>s "unavailable" then invalid_arg "synthesis timing links unsupported";ignore(text(get j "reason"))) ["timing_links";"electrical_links"];
           let geometry=get row "geometry" in
           Resource_physical_evidence.validate_geometry ~refs geometry;
           if get geometry "status"=s "available" then (
             if get (get o "physical") "status"<>s "available" then invalid_arg "resource geometry without available physical observation";
             if get o "stage"<>s "final_physical" || get provenance "completed_stage"<>s "full" || get row "status"<>s "matched" then invalid_arg "geometry without matched final resource";
             let wanted=list(get (get row "realized") "instances") |> List.map(fun i -> get i "instance",get i "module") in
             let actual=list(get geometry "instances") |> List.map(fun i -> get i "instance",get i "module") in
             if List.sort compare wanted<>List.sort compare actual then invalid_arg "geometry resource identity mismatch";
             let area=get row "area" in
             if get area "unit"<>s "um^2" then invalid_arg "geometry resource area unit mismatch";
             let area_refs=list(get area "evidence_refs") and geometry_refs=list(get geometry "evidence_refs") in
             if area_refs=[] || not(List.for_all(fun ref_ -> List.mem ref_ area_refs) geometry_refs) then
               invalid_arg "geometry resource area lacks geometry proof references";
             if get area "value"<>get geometry "area_um2" then invalid_arg "geometry resource area mismatch"));
        let declared=List.find (fun r -> get r "resource_id"=get row "resource_id") inventory_rows in
        List.iter (fun key -> if not (equal (get row key) (get declared key)) then invalid_arg ("observation contradicts resource inventory: "^key)) inventory_keys;
        List.iter (fun ref_ -> if not (List.mem ref_ refs) then invalid_arg "unclosed resource row reference") (list (get row "evidence_refs"));
        check_measurement (get row "area");
        let kind=text (get (get row "selected_implementation") "kind") in
        if not (List.mem kind ["macro";"flops"]) then invalid_arg "unknown selected implementation";
        let status=text (get row "status") in
        if not (List.mem status ["matched";"missing";"unexpected";"unresolved"]) then invalid_arg "unsupported resource outcome (optimization proof not supported)";
        if kind="macro" then (
          let declared=List.find (fun e -> get e "resource_id"=get row "resource_id") expected_inventory in
          if not (equal declared (get row "expected")) then invalid_arg "resource expectation differs from inventory");
        if kind="macro" && status="matched" then (
          let expected=get row "expected" and realized=get row "realized" in
          if get expected "resource_id"<>get row "resource_id" || get expected "retention_requirement"<>s "required" then invalid_arg "resource expectation ownership/retention mismatch";
          let wanted=list (get expected "instances") |> List.map (fun i -> text (get i "instance"),text (get i "module")) in
          let actual=list (get realized "instances") |> List.map (fun i -> text (get i "instance"),text (get i "module")) in
          unique "realized resource instance" (List.map fst actual);
          if wanted=[] || List.exists (fun (_,typ) -> s typ<>get (get row "selected_implementation") "name") wanted ||
             List.sort compare wanted<>List.sort compare actual || get expected "count"<>`Int (List.length wanted) ||
             get realized "count"<>`Int (List.length actual) then invalid_arg "matched resource does not match exact instance inventory")) resources;
      List.iter (fun typ ->
        let matched=List.filter (fun r -> get r "status"=s "matched" && get (get r "selected_implementation") "name"=s typ) resources in
        let count=List.fold_left (fun n row -> n+List.length (list (get (get row "realized") "instances"))) 0 matched in
        let observed=match observed_types with None -> 0. | Some types ->
          List.fold_left (fun n t -> if get t "module"=s typ then n+.numeric "macro type count" (get t "count") else n) 0. types in
        if float_of_int count>observed then invalid_arg "matched resource rows exceed observed macro type count";
        if get o "status"=s "pass" && float_of_int count<>observed then invalid_arg "passing resource rows do not cover observed macro instances") macro_types;
      if get o "status"=s "pass" && (match observed_types with None -> true | Some ts -> List.exists (fun t -> get t "classification"=s "unknown") ts) then invalid_arg "passing resource check has incomplete instance coverage") observations;
     if enriched then List.iter(fun o ->
       if get o "stage"=s "final_physical" && get (get o "physical") "status"=s "available" then (
         let geometries=List.map(fun row -> get row "geometry") (list(get o "resources")) in
         let sum=List.fold_left(fun n g -> if get g "status"=s "available" then n+.numeric "geometry area" (get g "area_um2") else n) 0. geometries in
         let measurements=get (get o "physical") "measurements" in
         let total=numeric "macro footprint total" (get (get measurements "macro_footprint_area") "value") in
         if abs_float(sum-.total)>1e-8 *. max 1. total then invalid_arg "physical aggregate differs from resource geometry";
         let count=get (get measurements "macro_component_count") "value" in
         if count<>`Null then (
           let observed=List.fold_left(fun n g -> if get g "status"=s "available" then n+List.length(list(get g "instances")) else n) 0 geometries in
           if numeric "macro component count" count<>float_of_int observed then invalid_arg "physical macro count differs from resource geometry"))) observations;
     let checks=list (get evidence "check_outcomes") in
    if List.length checks<>List.length observations then invalid_arg "missing resource check outcome";
    List.map2 (fun o c ->
      exact c ["stage";"status";"required"];
      if get c "stage"<>get o "stage" || get c "status"<>get o "status" then invalid_arg "resource check/observation mismatch";
      let required=List.exists (fun row -> get (get row "selected_implementation") "kind"=s "macro") (list (get o "resources")) || list (get o "unexpected_instances")<>[] in
      if get c "required"<>`Bool required then invalid_arg "resource check requirement mismatch";
      let status=text (get c "status") in
      if not (List.mem status ["pass";"fail";"not_applicable"]) then invalid_arg "unknown resource check outcome";
      if status="pass" && (list (get o "unexpected_instances")<>[] || List.exists (fun row -> get (get row "selected_implementation") "kind"=s "macro" && get row "status"<>s "matched") (list (get o "resources"))) then invalid_arg "passing resource check contradicts observations";
      if required && status<>"pass" then Some (text (get c "stage")^" required resource reconciliation failed") else None) observations checks |> List.filter_map Fun.id
let validate evidence = protect (fun () -> ignore (evaluate evidence);evidence)
let problems evidence = match protect (fun () -> evaluate evidence) with Ok problems -> problems | Error e -> [e]
let verify_sources ?context ~run_dir evidence = protect (fun () ->
   let evidence=normalize evidence in
   ignore (evaluate evidence);
  let root=Unix.realpath run_dir in
  List.iter (fun ref_ ->
    let path=Unix.realpath (Filename.concat root (text (get ref_ "path"))) in
    if not (String.starts_with ~prefix:(root^"/") path) then invalid_arg "resource evidence source escapes selected run";
    match Checkpoint.file_record path with
    | Ok f when get ref_ "sha256"=s f.sha256 -> ()
    | _ -> invalid_arg ("resource evidence source changed: "^text (get ref_ "path"))) (list (get evidence "evidence_refs"));
   let schema_version=match get evidence "schema_version" with `Int 1 -> 1 | _ -> 2 in
   let replay=run ?context ~schema_version ~run_dir () in
   ignore (evaluate replay);
   (* JSON object member order is not assertion semantics. In particular schema-1
      records may have been serialized by a different consumer. Arrays, source
      ordering and all values remain significant. Duplicate keys were rejected. *)
   let rec canonical = function
     | `Assoc xs -> `Assoc (List.sort compare (List.map(fun (k,v) -> k,canonical v) xs))
     | `List xs -> `List(List.map canonical xs)
     | x -> x in
   let actual=canonical(normalize evidence) and expected=canonical(normalize replay) in
   if E.record_bytes actual<>E.record_bytes expected then (
     let rec difference path a b =
       if E.record_bytes a=E.record_bytes b then None else match a,b with
       | `Assoc xs,`Assoc ys when List.map fst xs=List.map fst ys ->
         List.find_map(fun ((k,a),(_,b)) -> difference (path^"."^k) a b) (List.combine xs ys)
       | `List xs,`List ys when List.length xs=List.length ys ->
         List.find_map(fun (i,(a,b)) -> difference (path^"["^string_of_int i^"]") a b) (List.mapi(fun i x -> i,x) (List.combine xs ys))
       | _ -> Some path in
     invalid_arg ("resource evidence assertion differs from source-aware reconciliation replay at "^
       Option.value (difference "$" actual expected) ~default:"$")))
let inventory_available ~run_dir = protect (fun () ->
  let r={root=Unix.realpath run_dir;refs=[];bytes=0} in
  match first r manifest_paths with None -> false | Some path ->
    let manifest,_=json r path in
    Manifest_metadata.validate manifest;
    List.exists (fun key -> get manifest key<>`Null) ["resources";"resource_snapshot";"macro_contract"])
let collect ?context ~run_dir () = let evidence=run ?context ~run_dir () in ignore (evaluate evidence);evidence
let inspect ~run_dir = protect (fun () -> collect ~run_dir ())
