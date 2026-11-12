module J = Identity_json
let checked = function Ok x -> x | Error e -> invalid_arg e
let codec = function Ok x -> x | Error e -> invalid_arg (J.error_message e)
let str s = codec (J.string s)
let obj xs = codec (J.object_ xs)
let int n = codec (J.integer (string_of_int n))
let get j k = match j with J.Object xs -> Option.value (List.assoc_opt k xs) ~default:J.null | _ -> J.null
let text = function J.String s -> s | _ -> invalid_arg "expected string"
let array = function J.Array xs -> xs | _ -> invalid_arg "expected array"
let sha s = Cryptokit.hash_string (Cryptokit.Hash.sha256 ()) s |> Cryptokit.transform_string (Cryptokit.Hexa.encode ())
let contains s pattern = try ignore (Str.search_forward (Str.regexp_string pattern) s 0);true with Not_found -> false
let starts = String.starts_with
let words s = Str.split (Str.regexp "[ \t\r]+") (String.trim s)
let finite s = match float_of_string_opt s with Some f when Float.is_finite f -> Some f | _ -> None
let number = function None -> J.null | Some f -> codec (J.float f)
let nullable = function None -> J.null | Some s -> str s
let field_pairs xs = J.array (List.map (fun (k,v) -> J.array [str k;str v]) xs)
let protect f = try Ok (f ()) with
  | Invalid_argument s | Failure s | Sys_error s -> Error s
  | End_of_file -> Error "inspection source truncated while reading"
  | Unix.Unix_error (e,f,p) -> Error (f^" "^p^": "^Unix.error_message e)
let limit n s = if String.length s<=n then s else (
  let cut=ref n in
  while !cut>0 && Char.code s.[!cut] land 0xc0=0x80 do decr cut done;
  String.sub s 0 !cut ^ "\n[truncated]")
(* Reports can contain binary Liberty comments or arbitrary net-name bytes. *)
let printable s = match J.string s with Ok _ -> s | Error _ ->
  let b=Buffer.create (String.length s) in
  String.iter (fun c -> if Char.code c<128 && c<>'\000' then Buffer.add_char b c
    else Buffer.add_string b (Printf.sprintf "\\x%02x" (Char.code c))) s;Buffer.contents b

let bounded_lines ~max_lines raw =
  let count=ref 1 and i=ref 0 in
  while !i<String.length raw && !count<=max_lines do
    if raw.[!i]='\n' then incr count;
    incr i
  done;
  if !count>max_lines then Error "source line bound exceeded"
  else Ok (Array.of_list (String.split_on_char '\n' raw))

let corner_header line =
  if starts ~prefix:"=" (String.trim line) && contains line " Corner " then
    let rec find = function name::"Corner"::_ -> Some name | _::xs -> find xs | [] -> None in
    find (words line)
  else None

let timing_records ?(max_records=4096) raw =
  if String.length raw>32*1024*1024 then [],["timing source byte bound exceeded"] else
  match bounded_lines ~max_lines:262144 raw with Error message -> [],["timing "^message] | Ok lines ->
  let records=ref [] and diagnostics=ref [] and first=ref None and corner=ref None and count=ref 0 in
  let diagnostic s=if List.length !diagnostics<256 then diagnostics:=s::!diagnostics in
  let parse a b =
    let fields=ref (match !corner with None -> [] | Some c -> ["corner",c]) in
    (* The arithmetic summary repeats arrival with its subtraction sign. Keep
       the first actual arrival/required measurement; raw detail keeps both. *)
    let add k v=if not (List.mem_assoc k !fields) then fields:= !fields@[k,v] in
    for i=a to b do
      let s=String.trim lines.(i) in
      List.iter (fun (prefix,key) -> if starts ~prefix s then add key
        (String.sub s (String.length prefix) (String.length s-String.length prefix) |> String.trim))
        ["Startpoint:","startpoint";"Endpoint:","endpoint";"Path Group:","group";"Path Type:","type"];
      List.iter (fun (suffix,key) -> if String.ends_with ~suffix s then match words s with
        | v::_ -> add key v | _ -> ()) ["data arrival time","arrival";"data required time","required"];
      if contains s "slack (" then (match words s with v::_ -> add "slack" v | _ -> ());
      if contains s "slack (VIOLATED)" then add "status" "violated";
      if contains s "slack (MET)" then add "status" "met"
    done;
    if List.for_all (fun k -> List.mem_assoc k !fields) ["startpoint";"endpoint";"group";"type";"arrival";"required";"slack";"status"] &&
       List.for_all (fun k -> Option.is_some (finite (List.assoc k !fields))) ["arrival";"required";"slack"]
    then (incr count;records:=(a+1,b+1,!fields)::!records)
    else diagnostic (Printf.sprintf "malformed timing path lines %d-%d: missing header/slack or invalid quantity" (a+1) (b+1)) in
  let i=ref 0 in
  while !i<Array.length lines && !count<max 0 (min 4096 max_records) do
    let line=lines.(!i) in
    (match corner_header line with Some _ as found -> corner:=found | None -> ());
    if starts ~prefix:"Startpoint:" (String.trim line) then (
      Option.iter (fun a -> diagnostic (Printf.sprintf "malformed timing path lines %d-%d: missing slack" (a+1) !i)) !first;
      first:=Some !i);
    (if contains line "slack (" then match !first with None -> () | Some a -> parse a !i;first:=None);
    incr i
  done;
  while !i<Array.length lines && String.trim lines.(!i)="" do incr i done;
  if !i<Array.length lines then
    diagnostics:="timing record bound exceeded; remaining records not parsed"::!diagnostics;
  Option.iter (fun a -> diagnostic (Printf.sprintf "malformed timing path line %d: truncated before slack" (a+1))) !first;
  List.rev !records,List.rev !diagnostics

let registered_class id = match String.rindex_opt id '-' with
  | Some k when k+1<String.length id && String.for_all (function '0'..'9' -> true | _ -> false)
      (String.sub id (k+1) (String.length id-k-1)) -> String.sub id 0 k
  | _ -> id

let admit_input ~used_bytes ~source_count ~bytes =
  if bytes<0 || bytes>32*1024*1024 then Error "inspection per-source byte bound exceeded"
  else if source_count>=512 then Error "inspection source count bound exceeded (512)"
  else if used_bytes>64*1024*1024-bytes then Error "inspection aggregate input byte bound exceeded (64MiB)"
  else Ok (used_bytes+bytes,source_count+1)

exception Input_limit of string
exception Query_stop of string

let directory_names ~limit dir =
  let handle=Unix.opendir dir in
  Fun.protect ~finally:(fun () -> Unix.closedir handle) (fun () ->
    let rec loop count names = match Unix.readdir handle with
      | "." | ".." -> loop count names
      | name -> if count>=limit then raise (Input_limit ("inspection directory entry bound exceeded: "^dir));
        loop (count+1) (name::names)
      | exception End_of_file -> List.sort String.compare names in
    loop 0 [])

let clock_records ?(max_records=4096) raw =
  if String.length raw>32*1024*1024 then [],["clock source byte bound exceeded"] else
  match bounded_lines ~max_lines:262144 raw with Error message -> [],["clock "^message] | Ok lines ->
  let rows=ref [] and diagnostics=ref [] and count=ref 0 in
  let clock=ref None and transition=ref None and network_column=ref None and relationships=Hashtbl.create 16 in
  let diagnostic i message=if List.length !diagnostics<256 then
    diagnostics:=(Printf.sprintf "clock report line %d: %s" (i+1) message)::!diagnostics in
  let select ?(header=false) name =
    clock:=(if String.trim name="" then None else Some (String.trim name));transition:=None;network_column:=None;
    if header then Option.iter (Hashtbl.remove relationships) !clock in
  let emit i category label fields = match !clock with
    | None -> diagnostic i ("missing clock context for "^label)
    | Some name ->
      if !count>=max 0 (min 4096 max_records) then raise (Query_stop "clock record bound exceeded; remaining records not parsed");
      incr count;
      let inherited=Option.value (Hashtbl.find_opt relationships name) ~default:[] in
      rows:=(i+1,("category",category)::("name",name^" "^Option.value !transition ~default:""^" "^label)::
        ("clock",name)::("transition",Option.value !transition ~default:"")::fields@inherited)::!rows in
  let quantity i ?(edge=false) label value fields =
    if edge && !transition=None then diagnostic i ("missing transition context for "^label)
    else emit i "clock_network" label (("quantity",label)::("raw_value",value)::fields) in
  let parse i line =
    let t=String.trim line in
    if starts ~prefix:"Clock:" t then select ~header:true (String.sub t 6 (String.length t-6))
    else match words t with
    | ["Clock"] -> select "";diagnostic i "missing clock name"
    | ["Clock";name] -> select name
    | [a;"->";b] -> network_column:=None;
      if !clock=None then (transition:=None;diagnostic i "transition without clock context") else transition:=Some (a^" -> "^b)
    | ["min";"max"] ->
      let a=Str.search_forward (Str.regexp_string "min") line 0 in
      let b=Str.search_forward (Str.regexp_string "max") line 0 in
      network_column:=Some ((a+b)/2)
    | [a;b;"latency"] -> quantity i ~edge:true "latency_min" a [];quantity i ~edge:true "latency_max" b []
    | [a;b;"source";"latency"] -> quantity i ~edge:true "source_latency_min" a [];quantity i ~edge:true "source_latency_max" b []
    | [a;"network";"latency";pin] ->
      (match !network_column with None -> diagnostic i "missing min/max column context for network latency" | Some column ->
        let first=ref 0 in while !first<String.length line && (line.[!first]=' ' || line.[!first]='\t') do incr first done;
        quantity i ~edge:true (if !first>=column then "network_latency_max" else "network_latency_min") a ["endpoint",pin])
    | [a;"skew"] -> quantity i ~edge:true "network_skew" a []
    | ["Period:";a] -> quantity i "period" a []
    | [name;"period_min";"=";a;"fmax";"=";b] ->
      select name;quantity i "minimum_period" a [];quantity i "fmax" b []
    | _ ->
      let known=List.find_map (fun (prefix,key) -> if starts ~prefix t then
        let value=String.sub t (String.length prefix) (String.length t-String.length prefix) |> String.trim in
        Some (key,value,[key,value]) else None)
        ["Sources:","sources";"Generated:","generated";"Virtual:","virtual";"Propagated:","propagated";
         "Master Clock:","master_clock";"Source Clock:","source_clock";"Divide By:","divide_by";
         "Multiply By:","multiply_by";"Edges:","edges";"Edge Shifts:","edge_shifts";"Invert:","invert"] in
      let header=match known with Some _ -> known | None ->
        (* Preserve supplied extension headers verbatim, without assigning
           unsupported generation semantics or allowing identity-key clashes. *)
        match String.index_opt t ':' with
        | Some k when k>0 && k<=64 && (match t.[0] with 'A'..'Z'|'a'..'z' -> true | _ -> false) ->
          let label=String.sub t 0 k in
          let value=String.sub t (k+1) (String.length t-k-1) |> String.trim in
          let key="header:"^label in Some (key,value,["header_key",label;"header_value",value;key,value])
        | _ -> None in
      Option.iter (fun (key,value,fields) ->
        (match !clock with None -> () | Some name ->
          let previous=Option.value (Hashtbl.find_opt relationships name) ~default:[] in
          if not (List.mem_assoc key previous) && List.length previous>=32 then
            raise (Query_stop "clock header relationship bound exceeded (32)");
          Hashtbl.replace relationships name ((key,value)::List.remove_assoc key previous));
        emit i "clock_header" key fields) header in
  (try Array.iteri parse lines with Query_stop message -> diagnostics:=message::!diagnostics);
  List.rev !rows,List.rev !diagnostics

(* Native OpenROAD metrics are a flat line-oriented object. Preserve every
   repeated measurement with its own span; never apply JSON last-key-wins.
   This deliberately does not accept arbitrary nested native JSON. *)
let native_entries ?(max_entries=4096) raw =
  (* Validate commas/braces/depth with the evidence grammar. Its normalized
     value is deliberately discarded; entries below retain repeated spans. *)
  let syntax=if String.length raw>2*1024*1024 || Result.is_error (bounded_lines ~max_lines:8192 raw) then
    Error "native metrics byte/line bound exceeded" else Evidence_json.parse raw in
  match syntax with
  | Error e -> [],["malformed native metrics: "^e]
  | Ok _ ->
  let entries=ref [] and diagnostics=ref [] and counts=Hashtbl.create 32 and emitted=ref 0 in
  let diagnostic s=if List.length !diagnostics<256 then diagnostics:=s::!diagnostics in
  let lines=String.split_on_char '\n' raw in
  if String.trim raw="" || not (starts ~prefix:"{" (String.trim raw)) || not (String.ends_with ~suffix:"}" (String.trim raw))
  then [],["malformed native metrics: expected flat object"] else (
  (try List.iteri (fun i line -> let s=String.trim line in
    if s<>"" && s<>"{" && s<>"}" then (
      if !emitted>=max 0 (min 4096 max_entries) then raise (Query_stop "native entry bound exceeded; remaining entries not parsed");
      let s=if String.ends_with ~suffix:"," s then String.sub s 0 (String.length s-1) else s in
      try
      if String.length s>65536 then invalid_arg "native entry exceeds 64KiB";
      let document=checked (Evidence_json.parse ~reject_duplicates:true ("{"^s^"}")) in
      match Evidence_json.value document with
      | `Assoc [k,((`Int _|`Intlit _|`Float _|`String _|`Null) as v)] ->
        let n=1+Option.value (Hashtbl.find_opt counts k) ~default:0 in Hashtbl.replace counts k n;
        if n>16 then diagnostic ("native repeated key bound exceeded: "^k)
        else (if n>1 then diagnostic ("native repeated key preserved (occurrence "^string_of_int n^"): "^k);
          incr emitted;entries:=(i+1,k,v)::!entries)
      | _ -> diagnostic ("malformed native metrics line "^string_of_int (i+1)^": expected scalar entry")
      with Invalid_argument e -> diagnostic ("malformed native metrics line "^string_of_int (i+1)^": "^e))) lines
    with Query_stop message -> diagnostics:=message::!diagnostics);
  List.rev !entries,List.rev !diagnostics)

let liberty_time_units = Liberty_unit_evidence.parse

type source = {path:string; raw:string; hash:string; lines:string array}
type producer = {id:string; class_id:string; occurrence:int; boundary:string; dir:string; config:J.t; config_source:source; run_dir:string}
type row = {json:J.t; detail:string}
let scope = "observed supplementary-not-checkpoint-inventoried"
let within root p = p=root || starts ~prefix:(root^"/") p
let stable (a:Unix.stats) (b:Unix.stats) = a.st_dev=b.st_dev && a.st_ino=b.st_ino && a.st_size=b.st_size && a.st_mtime=b.st_mtime && a.st_ctime=b.st_ctime
let read_source ?root ?(admit=(fun _ -> ())) path =
  let canonical=Unix.realpath path in
  Option.iter (fun root ->
    if not (within root canonical) then invalid_arg ("symlink escape: "^path);
    if path<>canonical then invalid_arg ("supplementary source symlink/lexical alias is ambiguous: "^path)) root;
  let fd=Unix.openfile canonical [Unix.O_RDONLY;Unix.O_NONBLOCK] 0 in
  let ic=Unix.in_channel_of_descr fd in
  let raw=Fun.protect ~finally:(fun () -> close_in_noerr ic) (fun () ->
    let before=Unix.fstat fd in
    if before.st_kind<>Unix.S_REG then invalid_arg ("source is not a regular file: "^canonical);
    if before.st_size>32*1024*1024 then raise (Input_limit ("inspection per-source byte bound exceeded: "^canonical));
    admit before.st_size;
    let raw=really_input_string ic before.st_size in
    if not (stable before (Unix.fstat fd) && stable before (Unix.stat canonical)) || Unix.realpath path<>canonical then invalid_arg ("source changed while reading: "^canonical);
    raw) in
  let lines=match bounded_lines ~max_lines:262144 raw with Ok lines -> lines | Error message -> raise (Input_limit (message^": "^canonical)) in
  {path=canonical;raw;hash=sha raw;lines}
let source_ref s = obj ["path",str s.path;"sha256",str s.hash]
let excerpt s first last =
  let b=Buffer.create 1024 in Buffer.add_string b (s.path^"\n");
  let stop=ref false in
  for i=max 1 (first-2) to min (Array.length s.lines) (last+2) do
    if Buffer.length b<60000 then Buffer.add_string b (Printf.sprintf "%d: %s\n" i (limit 10000 (printable s.lines.(i-1)))) else stop:=true
  done;
  if !stop then Buffer.add_string b "[excerpt truncated]\n";limit 64000 (Buffer.contents b)

let run ~execution ~domain ~stage ?row_id ?expected_digest ?(refresh_timing=false) () = protect (fun () ->
  if refresh_timing && (domain<>"timing" || not (List.mem stage ["timing";"signoff"])) then
    invalid_arg "refresh-timing requires domain timing and stage timing or signoff";
  if not (List.mem domain ["timing";"clocks";"physical";"routing"]) then invalid_arg "invalid inspection domain";
  let stages=["synthesis";"floorplan";"placement";"cts";"routing";"timing";"signoff"] in
  if not (List.mem stage stages) then invalid_arg "invalid inspection stage";
  if Option.is_some row_id && Option.is_none expected_digest then invalid_arg "row-id requires expected-digest";
  let input_bytes=ref 0 and source_count=ref 0 in
  let admit bytes=match admit_input ~used_bytes:!input_bytes ~source_count:!source_count ~bytes with
    | Error message -> raise (Input_limit message)
    | Ok (bytes,count) -> input_bytes:=bytes;source_count:=count in
  let execution_source=try read_source ~admit execution with Input_limit message -> invalid_arg message in
  let observed=ref [source_ref execution_source] in
  (* Reuse the report service's exact checkpoint, compatibility, boundary,
     closure and resolved-value checks before observing supplementary bytes. *)
  let metadata_seen=Hashtbl.create 32 in
  let manifest_values=Hashtbl.create 8 in
  let admit_metadata path =
    if not (Hashtbl.mem metadata_seen path) then (
      if Filename.basename path="manifest.json" then (
        let source=read_source ~admit path in
        Hashtbl.add manifest_values path (codec (J.parse source.raw));
        observed:=source_ref source::!observed)
      else (
        let stat=Unix.stat path in
        if stat.st_kind<>Unix.S_REG then invalid_arg ("checkpoint metadata is not a regular file: "^path);
        admit stat.st_size);
      Hashtbl.add metadata_seen path ()) in
  let record,manifests=try checked (Stage_report_service.validate_execution_snapshot ~admit_metadata
    (codec (J.parse execution_source.raw))) with Input_limit message -> invalid_arg message in
  List.iter (fun (path,manifest) ->
    if Hashtbl.find_opt manifest_values (Filename.concat path "manifest.json")<>Some manifest.Checkpoint.raw then
      invalid_arg "checkpoint manifest changed between capture and validation") manifests;
  let timing_alias_recheck=ref (fun () -> ()) in
  (if domain="timing" && stage="synthesis" then
    match checked (Timing_intent_execution.inspect_snapshot ~execution:execution_source.path
      ~execution_sha256:execution_source.hash ~raw:record.raw ~record ~checkpoints:manifests ()) with
    | None -> ()
    | Some evidence ->
      (match evidence with `Assoc fields -> (match List.assoc_opt "association" fields with
        | Some (`Assoc association) -> (match List.assoc_opt "external_aliases" association with
          | Some rows ->
            let aliases=Timing_intent_inventory.aliases_of_json rows in
            let checkpoint=match List.find_opt (fun (_,m)->m.Checkpoint.boundary=Execution_plan.Stage.Synthesis) manifests with
              | Some (_,m)->m | None->invalid_arg "missing captured timing alias checkpoint" in
            let roots=[text (get checkpoint.compatibility "pdk_root");text (get checkpoint.compatibility "project")^"/tt"] in
            timing_alias_recheck:=(fun () -> Timing_intent_inventory.validate_aliases ~roots ~external_files:checkpoint.external_files aliases)
          | None -> ())
        | _ -> ()) | _ -> ());
      let evidence=codec (J.parse (Yojson.Safe.to_string evidence)) in
      let sources=array (get evidence "sources") in
      List.iter (fun source -> let path=text (get source "path") in
        if path<>execution_source.path && not (Hashtbl.mem metadata_seen path) then (
          admit (Unix.stat path).Unix.st_size;
          Hashtbl.add metadata_seen path ())) sources;
      observed:=List.rev_append sources !observed);
  let diagnostics=ref [] and rows=ref [] and size=ref 0 and truncated=ref false in
  let diagnostic s = if List.length !diagnostics<256 then diagnostics:= !diagnostics@[limit 1500 (printable s)] else truncated:=true in
  let observe s = observed:=source_ref s::!observed;s in
   let report_bytes=ref 0 and report_count=ref 0 in
   let admit_report bytes =
     if bytes<0 || bytes>32*1024*1024 || !report_count>=128 || !report_bytes>256*1024*1024-bytes then
       raise (Input_limit "timing reinspection report byte/count bound exceeded");
     incr report_count;report_bytes:= !report_bytes+bytes in
   let read root p=read_source ~root
     ~admit:(if refresh_timing && String.ends_with ~suffix:".rpt" p then admit_report else admit) p |> observe in
   let attempt= text (get record.raw "attempt_id") in
   let lineage=ref [] in
  let analysis =
    if domain = "timing" && List.mem stage [ "timing"; "signoff" ]
    then (
      match
        Timing_analysis.load
          ~execution:execution_source.path
          ~execution_raw:execution_source.raw
      with
       | Some a when get a "stage" = str stage ->
         if not refresh_timing then Some a else (
           let original, refreshed=Timing_analysis.reinspect
             ~execution:execution_source.path ~expected_record:record.raw in
           let original_file=Timing_analysis.path (text (get original "execution_file")) in
           let original_source=read (Filename.dirname execution_source.path) original_file in
           if codec (J.parse original_source.raw)<>original then invalid_arg "original analysis changed during reinspection";
           observed:=List.rev_append (array (get original "inventory")) !observed;
           lineage:=["mode",str "read_only_reinspection";
             "original_observation",source_ref original_source;
             "original_analysis_digest",get original "source_digest";
             "original_status",get original "status";
             "original_timing_goal",get original "timing_goal";
             "recomputed_analysis",refreshed];
           Some refreshed)
      | _ -> None)
    else None
   in
   if refresh_timing && analysis=None then invalid_arg "timing reinspection requires an observation for the selected stage";
   Option.iter (fun a ->
    Timing_analysis.validate_sources a;
     if not refresh_timing then (
       let captured=read (Filename.dirname execution_source.path) (Timing_analysis.path (text (get a "execution_file"))) in
       if codec (J.parse captured.raw)<>a then invalid_arg "timing analysis changed between validation and capture");
    observed:=List.rev_append (array (get a "inventory")) !observed) analysis;
  (match row_id,expected_digest with
   | None,Some expected -> (match analysis with
     | Some a when get a "source_digest"=str expected -> ()
     | _ -> invalid_arg "stale or unavailable timing analysis source_digest")
   | _ -> ());
  let read root path =
    let s=read root path in
    Option.iter (fun a ->
      if not (List.exists (fun ref -> get ref "path"=str s.path && get ref "sha256"=str s.hash) (array (get a "inventory")))
      then invalid_arg ("source was not captured by timing analysis: " ^ path)) analysis;
    s in
  let provenance p s first last units = obj ["file",source_ref s;"span",obj ["first_line",int first;"last_line",int last];
    "step_id",str p.id;"occurrence",int p.occurrence;
    "boundary",str p.boundary;"carry_forward",J.bool (p.boundary<>stage);
     "evidence_scope",str (if refresh_timing then "read_only_reinspection_of_retained_sources_not_original_observation" else scope);
    "config",obj ["path",str (if Filename.dirname s.path=p.dir then "config.json" else "../config.json");"sha256",str p.config_source.hash];
    "relative_paths",str "report file directory";
    "units_source",units;
    "constraints_refs",str "config#/PNR_SDC_FILE,SIGNOFF_SDC_FILE";
    "netlist_refs",str "input_references rows: state_in.json#/nl,pnl"] in
  let add p s first last ~category ?corner ?group ~name ?value ?unit ?(status="observed") ?(fields=[]) ?(units=J.null) () =
    if List.length !rows>=4096 then raise (Query_stop "inspection row bound exceeded (4096); remaining reports not parsed");
    let source=provenance p s first last units |> J.canonical in
    let id=sha (domain^"\000"^source^"\000"^category^"\000"^name) in
    let json=obj ["id",str id;"category",str category;"producer",str (p.id^"@"^Filename.basename p.dir);
      "corner",nullable corner;"group",nullable group;"name",str (printable name);"value",number value;
      "unit",nullable unit;"status",str status;"source",str source;"fields",field_pairs (List.map (fun (k,v)->k,printable v) fields)] in
    let bytes=String.length (J.canonical json)+1 in
    if !size+bytes>1950000 then raise (Query_stop "inspection JSON byte bound exceeded; remaining reports not parsed")
    else (size:= !size+bytes;
      (* Index queries retain no report excerpts. A detail query retains only
         the requested row's bounded excerpt, from these exact observed bytes. *)
      let detail=if row_id=Some id then excerpt s first last else "" in
      rows:={json;detail}::!rows) in
  let perform () =
  let selected=if analysis<>None then [] else List.filter (fun r -> get r "boundary"=str stage) record.references in
  if selected=[] && analysis=None then diagnostic ("missing published checkpoint for stage "^stage);
  let rank s=let rec find i=function [] -> -1 | x::xs -> if x=s then i else find (i+1) xs in find 0 stages in
  let references=if domain="physical" && selected<>[] then List.filter (fun r -> rank (text (get r "boundary"))<=rank stage) record.references else selected in
  let producers=ref [] in
  Option.iter (fun a ->
    if get a "status"<>str "completed" then diagnostic ("post-PNR analysis unavailable: " ^ text (get a "reason"))
    else (
      let p=get a "producer" in
      let dir=text (get p "directory") and run_dir=text (get p "run_dir") in
      let config_source=read run_dir (dir^"/config.json") in
      let config=codec (J.parse config_source.raw) in
      let occurrence=match get p "ordinal" with J.Integer s -> int_of_string s | _ -> invalid_arg "invalid analysis ordinal" in
      producers:=[{id=text (get p "step_id");class_id="OpenROAD.STAPostPNR";occurrence;boundary=stage;dir;config;config_source;run_dir}])) analysis;
  let producer_count=ref 0 and corner_count=ref 0 and log_count=ref 0 in
  let relevant_class class_id =
    List.mem domain ["timing";"clocks"] && starts ~prefix:"OpenROAD.STA" class_id ||
    domain="physical" && List.mem class_id ["OpenROAD.Floorplan";"OpenROAD.DetailedPlacement"] ||
    domain="routing" && List.mem class_id ["OpenROAD.GlobalRouting";"OpenROAD.DetailedRouting";"OpenROAD.CheckAntennas";"OpenROAD.RepairAntennas";"Magic.DRC"] in
  List.iter (fun reference -> try
    let checkpoint=text (get reference "path") in
    let segment=read checkpoint (Filename.concat checkpoint "segment.json") in
    let manifest=match List.assoc_opt checkpoint manifests with Some m -> m | None -> invalid_arg "checkpoint absent from captured validation snapshot" in
    let inventory=match List.assoc_opt "segment.json" manifest.Checkpoint.files with Some f -> f | None -> invalid_arg "missing validated segment inventory" in
    let data=checked (Stage_report_service.decode_segment_bytes ~expected_sha256:inventory.sha256 segment.raw) in
    let boundary=text (get data "boundary") in
    let recorded_run_dir=text (get data "run_dir") in
    let run_dir=Unix.realpath recorded_run_dir in
    if recorded_run_dir<>run_dir then invalid_arg "recorded run_dir must be canonical (symlink aliases are not inspected)";
    let returned=array (get data "returned_steps") |> List.map text in
    producer_count:= !producer_count+List.length returned;
    if !producer_count>256 then raise (Input_limit "inspection producer count bound exceeded (256)");
    if List.length returned<>List.length (List.sort_uniq String.compare returned) then invalid_arg "ambiguous duplicate returned step IDs";
    let names=directory_names ~limit:1024 run_dir in
    let found=Hashtbl.create 32 in
    List.iter (fun name ->
      let numbered=match String.index_opt name '-' with None -> false | Some i -> i>0 && String.for_all (function '0'..'9' -> true | _ -> false) (String.sub name 0 i) in
      if numbered then (
        let dir=Filename.concat run_dir name in
        let dash=String.index name '-' in
        let ordinal=int_of_string (String.sub name 0 dash) in
        if ordinal<1 || ordinal>List.length returned then invalid_arg ("numbered producer outside returned_steps: "^dir);
        let id=List.nth returned (ordinal-1) in
        let base=registered_class id in
        if relevant_class base then (
        if not (within run_dir (Unix.realpath dir)) then invalid_arg ("symlink escape: "^dir);
        if Unix.realpath dir<>dir then invalid_arg ("ambiguous producer directory alias: "^dir);
        try
          let config_source=read run_dir (Filename.concat dir "config.json") in
          let config=codec (J.parse config_source.raw) in
          let meta=get config "meta" in
          let meta_id=match get meta "step" with J.String s -> s | _ -> text (get meta "id") in
          (* LibreLane config metadata records the registered class, whereas
             returned_steps records the occurrence-specific ID. The numbered
             producer is tied to the exact returned position, not recency. *)
          if meta_id<>id && meta_id<>base then invalid_arg ("producer metadata/returned_steps mismatch: "^dir^" "^id^" vs "^meta_id);
          if Hashtbl.mem found id then invalid_arg ("ambiguous producer metadata for "^id);
          (match get config "STA_CORNERS" with J.Array corners when
            List.mem base ["OpenROAD.STAPostPNR";"OpenROAD.STAPrePNR"] && List.length corners>64 ->
              raise (Input_limit "inspection corner count bound exceeded (64)") | _ -> ());
          let config=obj ["STA_CORNERS",get config "STA_CORNERS"] in
          let config_source={config_source with raw="";lines=[||]} in
          Hashtbl.add found id (dir,config,config_source)
        with Unix.Unix_error ((Unix.ENOENT|Unix.ENOTDIR),_,_) -> diagnostic ("missing producer config: "^dir)))) names;
    List.iteri (fun i id -> if relevant_class (registered_class id) then match Hashtbl.find_opt found id with
      | None -> diagnostic ("missing retained producer metadata: "^boundary^" "^id)
      | Some (dir,config,config_source) -> producers:= !producers@[{id;class_id=registered_class id;occurrence=i+1;boundary;dir;config;config_source;run_dir}]) returned
    with Unix.Unix_error ((Unix.ENOENT|Unix.ENOTDIR),_,path) -> diagnostic ("missing retained producer tree: "^path)) references;
  let attempt_read p path f =
    if List.length !rows>=4096 then raise (Query_stop "inspection row bound exceeded (4096); remaining reports not parsed");
    if String.ends_with ~suffix:".log" path then (incr log_count;
      if !log_count>256 then raise (Input_limit "inspection log count bound exceeded (256)"));
    try let s=read p.run_dir path in f s with
    | Unix.Unix_error ((Unix.ENOENT|Unix.ENOTDIR),_,_) -> diagnostic ("missing retained source: "^path)
    | End_of_file -> diagnostic ("malformed retained source (truncated during read): "^path)
    | Invalid_argument e | Failure e | Sys_error e -> diagnostic ("malformed retained source "^path^": "^e) in
  let report p ?corner category path = attempt_read p path (fun s ->
    add p s 1 (Array.length s.lines) ~category ?corner ~name:(Filename.basename path) ~status:"report" ()) in
  let report_corner_conflict corner s =
    let reported=ref [] in
    Array.iter (fun line -> match corner_header line with None -> () | Some name ->
      if not (List.mem name !reported) then (
        if List.length !reported>=64 then raise (Input_limit "inspection report corner header bound exceeded (64)");
        reported:=name::!reported)) s.lines;
    let conflict=match corner with None -> false | Some expected -> List.exists ((<>) expected) !reported in
    if conflict then diagnostic ("conflicting report/directory corner; report quarantined with no value/unit: "^s.path);
    conflict,(if conflict then ["directory_corner",Option.value corner ~default:"";
      "report_corners",String.concat "," (List.sort String.compare !reported);"corner_conflict","quarantined"] else []) in
  let unit_for p dir corner = try
    if not (within p.run_dir (Unix.realpath dir)) then invalid_arg ("symlink escape: "^dir);
    let names=ref [] in
    attempt_read p (Filename.concat p.dir "state_out.json") (fun s ->
      let document=checked (Evidence_json.parse ~reject_duplicates:true s.raw) in
      let library=match Evidence_json.value document with
        | `Assoc xs -> Option.value (List.assoc_opt "lib" xs) ~default:`Null
        | _ -> invalid_arg "output state must be an object" in
      let library=match corner,library with
        | Some corner,`Assoc xs -> Option.value (List.assoc_opt corner xs) ~default:`Null
        | None,`Assoc _ -> `Null (* No corner identity: never select an arbitrary library. *)
        | _,v -> v in
      match library with
      | `String path ->
        let path=Unix.realpath path in
        if not (within p.run_dir path) then diagnostic ("retained output library is outside exact run_dir (not read): "^path)
        else if Filename.dirname path<>Unix.realpath dir then diagnostic ("output library is not produced in selected STA directory (not used for units): "^path)
        else names:=[path]
      | `Null -> ()
      | _ -> invalid_arg "ambiguous or malformed output library reference");
    let units=ref [] and malformed=ref false in
    List.iter (fun path -> attempt_read p path (fun s ->
      let n=Filename.basename path in
      let declarations,ds=liberty_time_units s.raw in
      List.iter (fun d -> malformed:=true;diagnostic (s.path^": "^d)) ds;
      if List.exists (fun d -> contains d "bound exceeded") ds then raise (Query_stop "Liberty parser bound exceeded; remaining reports not parsed");
      List.iter (fun (line,unit,raw_line) ->
        units:=(unit,obj ["path",str n;"sha256",str s.hash;"line",int line;"declaration",str (String.trim (printable raw_line))])::!units) declarations)) !names;
    match List.sort_uniq String.compare (List.map fst !units) with
    | ["1ns"] when not !malformed -> Some "ns",J.array (List.map snd !units)
    | [] -> diagnostic ("missing retained timing unit declaration: "^dir);None,J.null
    | _ -> diagnostic ("unsupported or conflicting retained timing units: "^dir);None,J.array (List.map snd !units)
    with Unix.Unix_error ((Unix.ENOENT|Unix.ENOTDIR),_,_) -> diagnostic ("missing retained corner directory: "^dir);None,J.null in
  List.iter (fun p ->
    let sta=starts ~prefix:"OpenROAD.STA" p.class_id in
    let relevant=relevant_class p.class_id in
    if relevant then attempt_read p (Filename.concat p.dir "state_in.json") (fun s ->
      let document=checked (Evidence_json.parse ~reject_duplicates:true s.raw) in
      let fields=match Evidence_json.value document with
        | `Assoc xs -> List.filter_map (fun (k,v) -> if List.mem k ["nl";"pnl";"sdc";"odb";"def";"spef"] then Some (k,Yojson.Safe.to_string v) else None) xs
        | _ -> invalid_arg "input state must be an object" in
      add p s 1 (min 15 (Array.length s.lines)) ~category:"input_references" ~name:"Recorded design and constraints inputs" ~fields ());
    if sta && List.mem domain ["timing";"clocks"] then (
      let dirs=if p.class_id="OpenROAD.STAPostPNR" || p.class_id="OpenROAD.STAPrePNR" then (
        let corners=array (get p.config "STA_CORNERS") in
        if !corner_count+List.length corners>64 then raise (Input_limit "inspection corner count bound exceeded (64)");
        let corners=List.map text corners in
        if List.length corners<>List.length (List.sort_uniq String.compare corners) then invalid_arg "ambiguous duplicate STA corner directories";
        corners |> List.map (fun c ->
          if Filename.basename c<>c || c="." || c=".." then invalid_arg "invalid corner path";
          Some c,Filename.concat p.dir c))
        else [None,p.dir] in
      corner_count:= !corner_count+List.length dirs;
      if !corner_count>64 then raise (Input_limit "inspection corner count bound exceeded (64)");
      List.iter (fun (corner,dir) ->
        let unit,units=unit_for p dir corner in
        if domain="timing" then List.iter (fun file -> attempt_read p (Filename.concat dir file) (fun s ->
           let paths,ds=timing_records ~max_records:(if refresh_timing then min 32 (4096-List.length !rows) else 4096-List.length !rows) s.raw in
           List.iter (fun d -> if refresh_timing && contains d "bound exceeded" then truncated:=true;
             diagnostic (s.path^": "^d)) ds;
          if paths=[] && ds=[] then diagnostic ("missing timing path records: "^s.path);
          List.iter (fun (a,b,fields) ->
            let reported_corner=List.assoc_opt "corner" fields in
            let conflict=match corner,reported_corner with Some a,Some b -> a<>b | _ -> false in
            if conflict then diagnostic ("conflicting report/directory corner; record quarantined with no value/unit: "^s.path);
            let directory_corner=corner in
            let corner=match reported_corner with Some _ -> reported_corner | None -> corner in
            add p s a b ~category:"timing_path" ?corner
            ~group:(List.assoc "group" fields)
            ~name:((match words (List.assoc "startpoint" fields) with x::_ -> x | [] -> "?")^" -> "^
              (match words (List.assoc "endpoint" fields) with x::_ -> x | [] -> "?"))
             ?unit:(if conflict then None else unit) ~units:(if conflict then J.null else units)
             ?value:(if conflict then None else finite (List.assoc "slack" fields))
             ~status:(if conflict then "unavailable" else List.assoc "status" fields)
             ~fields:(if conflict then ("directory_corner",Option.value directory_corner ~default:"")::("corner_conflict","quarantined")::fields else fields) ()) paths;
           if not refresh_timing && List.exists (fun d -> contains d "bound exceeded") ds then raise (Query_stop "timing parser bound exceeded; remaining reports not parsed"))) ["max.rpt";"min.rpt"]
        else (
          attempt_read p (Filename.concat dir "clock.rpt") (fun s ->
            let conflict,conflict_fields=report_corner_conflict corner s in
            let records,ds=clock_records ~max_records:(4096-List.length !rows) s.raw in
            List.iter (fun d -> diagnostic (s.path^": "^d)) ds;
            List.iter (fun (line,fields) ->
              let raw_value=List.assoc_opt "raw_value" fields in
              let measured=Option.bind raw_value finite in
              let value=if conflict then None else measured in
              if raw_value<>None && measured=None then diagnostic ("malformed clock quantity: "^s.path^":"^string_of_int line);
              let quantity=List.assoc_opt "quantity" fields in
              let row_unit=if conflict || raw_value=None then None else if quantity=Some "fmax" then
                (if unit=Some "ns" then Some "MHz" else None) else unit in
              add p s line line ~category:(List.assoc "category" fields) ?corner
                ~name:(List.assoc "name" fields) ?value ?unit:row_unit ~units:(if conflict then J.null else units)
                ~status:(if conflict || raw_value<>None && measured=None then "unavailable" else "observed")
                ~fields:(conflict_fields@fields) ()) records;
            if List.exists (fun d -> contains d "bound exceeded") ds then raise (Query_stop "clock parser bound exceeded; remaining reports not parsed"));
          List.iter (fun file -> attempt_read p (Filename.concat dir file) (fun s ->
            let conflict,conflict_fields=report_corner_conflict corner s in
            Array.iteri (fun i line -> match words line with
              | [value;("setup"|"hold" as check);"skew"] ->
                let measured=finite value in
                if measured=None then diagnostic ("malformed clock check skew: "^s.path^":"^string_of_int (i+1));
                add p s (i+1) (i+1) ~category:"clock_check_skew" ?corner ~name:(check^" check skew (includes uncertainty and CRPR)")
                  ?value:(if conflict then None else measured) ?unit:(if conflict then None else unit)
                  ~units:(if conflict then J.null else units) ~status:(if conflict || measured=None then "unavailable" else "observed")
                  ~fields:(conflict_fields@["check",check;"raw_value",value]) ()
              | _ -> ()) s.lines)) ["skew.max.rpt";"skew.min.rpt"];
          List.iter (fun file -> report p ?corner "clock_coverage" (Filename.concat dir file)) ["checks.rpt";"unpropagated.rpt";"violator_list.rpt"];
          let sta_log=if Filename.dirname dir=p.dir then "sta.log" else
            match (try directory_names ~limit:1024 dir |> List.filter (fun n -> starts ~prefix:"openroad-sta" n && String.ends_with ~suffix:".log" n)
              with Unix.Unix_error ((Unix.ENOENT|Unix.ENOTDIR),_,_) -> []) with
            | [name] -> name | [] -> "sta.log" | _ -> invalid_arg "ambiguous retained STA log" in
          attempt_read p (Filename.concat dir sta_log) (fun s ->
            Array.iteri (fun i line -> if contains line "propagat" &&
              (contains line "[INFO" || contains line "[WARNING" || contains line "set_propagated_clock") then (
              diagnostic (Printf.sprintf "%s:%d: %s (clock header and STA propagation evidence retained separately)" s.path (i+1) line);
              add p s (i+1) (i+1) ~category:"clock_propagation" ?corner ~name:"STA propagation evidence" ~fields:["message",line] ())) s.lines))) dirs);
    if domain="physical" && List.mem p.class_id ["OpenROAD.Floorplan";"OpenROAD.DetailedPlacement"] ||
       domain="routing" && relevant_class p.class_id then (
      attempt_read p (Filename.concat p.dir "or_metrics_out.json") (fun s ->
        let entries,ds=native_entries ~max_entries:(4096-List.length !rows) s.raw in List.iter (fun d -> if contains d "bound exceeded" then truncated:=true;diagnostic (s.path^": "^d)) ds;
        List.iter (fun (line,key,v) ->
          let physical=starts ~prefix:"design__" key || key="route__wirelength__estimated" in
          let routing=starts ~prefix:"route__" key || contains key "antenna" || contains key "drc" in
          if domain="physical" && physical || domain="routing" && routing then (
            let value=match v with `Int n -> Some (float_of_int n) | `Intlit s -> finite s | `Float f when Float.is_finite f -> Some f | _ -> None in
            let unit=if contains key "utilization" then Some "ratio" else if contains key "area" then Some "µm²"
              else if contains key "wirelength" || contains key "displacement" || contains key "bbox" then Some "µm"
              else if contains key "count" || contains key "rows" || contains key "sites" || contains key "violations" || contains key "drc_errors" || starts ~prefix:"route__net" key || starts ~prefix:"route__vias" key || starts ~prefix:"antenna__" key then Some "count" else None in
            let raw=Yojson.Safe.to_string v in
            let geometry=match v with `String s when contains key "bbox" -> (
              match words s with [x0;y0;x1;y1] when List.for_all (fun x -> Option.is_some (finite x)) [x0;y0;x1;y1] &&
                float_of_string x1>=float_of_string x0 && float_of_string y1>=float_of_string y0 ->
                ["x0",x0;"y0",y0;"x1",x1;"y1",y1;
                 "width",string_of_float (float_of_string x1-.float_of_string x0);
                 "height",string_of_float (float_of_string y1-.float_of_string y0)]
              | _ -> diagnostic ("malformed native bbox: "^s);[]) | _ -> [] in
            let value=match value with
              | Some f when unit=Some "count" && (f<0. || floor f<>f) -> None
              | Some f when unit=Some "µm²" && f<0. -> None
              | v -> v in
            let status=match value with Some _ -> "observed" | None when geometry<>[] -> "geometry" | None ->
              diagnostic ("malformed/nonfinite native quantity: "^s.path^":"^key);"unavailable" in
            let fields=geometry@["raw",raw;"measurement_scope",(if key="route__wirelength__estimated" && p.class_id="OpenROAD.DetailedPlacement" then "legalized HPWL estimate before mirroring optimization; not routed length" else "native producer measurement")] in
            add p s line line ~category:(if domain="physical" then "physical_metric" else if contains key "antenna" then "antenna" else if contains key "drc" then "detailed_drc" else "routing_metric")
              ~name:key ?value ?unit ~status ~fields ~units:(str "OpenROAD native metric naming/producer report semantics") ())) entries;
        if List.exists (fun d -> contains d "entry bound exceeded") ds then raise (Query_stop "native parser bound exceeded; remaining reports not parsed"));
      (* A log filename is selected from actual retained entries, scoped to the
         metadata-validated producer, rather than a universal numbered dirname. *)
      let logs=directory_names ~limit:1024 p.dir |> List.filter (fun n -> String.ends_with ~suffix:".log" n && not (contains n "process_stats")) in
      if !log_count+List.length logs>256 then raise (Input_limit "inspection log count bound exceeded (256)");
      List.iter (fun n -> attempt_read p (Filename.concat p.dir n) (fun s ->
        Array.iteri (fun i line ->
          let t=String.trim line in
          if domain="physical" && (contains t "HPWL" || contains t "displacement") then (
            let ws=words t in let value=List.find_map finite ws in
            add p s (i+1) (i+1) ~category:"placement_analysis" ~name:t ?value
              ~unit:(if contains t "%" then "%" else "µm") ~fields:["semantics",(if contains t "HPWL after" then "post-mirroring optimized HPWL" else "native placement analysis, not routed length")] ());
          if domain="routing" then (
            (match words t with
             | [layer;resource;demand;usage;h;"/";v;"/";total] when Option.is_some (finite resource) && String.ends_with ~suffix:"%" usage ->
               add p s (i+1) (i+1) ~category:"global_congestion" ~name:layer ?value:(finite total) ~unit:"count"
                 ~fields:["layer",layer;"resource",resource;"demand",demand;"usage_percent",String.sub usage 0 (String.length usage-1);"max_horizontal_overflow",h;"max_vertical_overflow",v;"total_overflow",total] ()
             | _ -> ());
            let quantity marker unit =
              if contains t marker then (
                let k=Str.search_forward (Str.regexp_string marker) t 0 + String.length marker in
                let tail=String.sub t k (String.length t-k) |> words in
                let value=match tail with x::_ -> finite x | _ -> None in
                add p s (i+1) (i+1) ~category:"global_routing" ~name:marker ?value ~unit ~fields:["raw",t] ()) in
            quantity "Total wirelength:" "µm";quantity "Routed nets:" "count";quantity "Final number of vias:" "count";
            if contains t "Start extra iteration" then (
              let re=Str.regexp "iteration \\([0-9]+\\)/\\([0-9]+\\)" in
              if (try ignore (Str.search_forward re t 0);true with Not_found -> false) then
                add p s (i+1) (i+1) ~category:"global_routing" ~name:"overflow removal iteration" ?value:(finite (Str.matched_group 1 t)) ~unit:"count" ~fields:["iteration_limit",Str.matched_group 2 t] ());
            if contains t "violations" || contains t "antenna" then
              add p s (i+1) (i+1) ~category:(if contains p.class_id "Antenna" then "antenna" else if p.class_id="OpenROAD.DetailedRouting" then "detailed_routing" else "global_routing") ~name:t ~fields:["raw",t] ())) s.lines)) logs)) !producers in
  (try perform () with Input_limit message | Query_stop message -> truncated:=true;diagnostic message);
  let budget=2*1024*1024-8192-String.length (J.canonical (J.array (List.map str !diagnostics))) in
  let rec bounded used = function
    | [] -> []
    | r::rest -> let bytes=String.length (J.canonical r.json)+1 in
      if used+bytes>budget then (truncated:=true;[]) else r::bounded (used+bytes) rest in
  let rows=bounded 0 (List.rev !rows) in
  if rows=[] then diagnostic "missing relevant retained evidence rows (not a zero measurement)";
   let digest=sha (J.canonical (obj (["execution",source_ref execution_source;"domain",str domain;"stage",str stage;
     "observed",J.array (List.rev !observed);"rows",J.array (List.map (fun r -> r.json) rows);
     "diagnostics",J.array (List.map str !diagnostics)] @
     (if refresh_timing then ["reinspection",obj !lineage] else [])))) in
  let identity=["contract",str "hardcaml-asic.inspection";"schema_version",int 1;
    "execution_file",str execution_source.path;"execution_sha256",str execution_source.hash;"attempt_id",str attempt;
    "domain",str domain;"stage",str stage;"source_digest",str digest;
    "diagnostics",J.array (List.map str !diagnostics);"truncated",J.bool !truncated] @
     (if refresh_timing then ["reinspection",obj !lineage] else []) @
     (match analysis with None -> [] | Some a -> ["analysis_digest",get a "source_digest";"analysis_id",get a "analysis_id"]) in
  let result=match row_id,expected_digest with
   | None,_ -> obj (identity@["rows",J.array (List.map (fun r -> r.json) rows)])
  | Some id,Some expected ->
    if expected<>digest then invalid_arg "stale inspection source_digest: refresh the index";
    let row=match List.find_opt (fun r -> get r.json "id"=str id) rows with Some r -> r | None -> invalid_arg "unknown inspection row_id" in
    let identity=List.map (fun (key,value) -> if key="truncated" then
      key,J.bool (!truncated || contains row.detail "[truncated]" || contains row.detail "[excerpt truncated]") else key,value) identity in
    obj (identity@["row_id",str id;"detail",str row.detail])
  | _ -> assert false in
  if String.length (J.canonical result)>2*1024*1024 then invalid_arg "inspection envelope bound exceeded";
  (* Recheck every observed file: a mixed generation never becomes an index. *)
  List.iter (fun ref -> let path=text (get ref "path") in
    if (checked (Checkpoint.file_record path)).sha256<>text (get ref "sha256") then invalid_arg ("inspection source changed: "^path)) !observed;
  (!timing_alias_recheck) ();
  result)
