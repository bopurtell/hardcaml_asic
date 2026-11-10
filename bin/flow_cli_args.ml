(* Public CLI4 grammar. Helper-private options deliberately never enter this table. *)
type t = { command : string; query : string; values : (string,string) Hashtbl.t;
           flags : (string,bool) Hashtbl.t; mutable waivers : string list }
exception Usage of string
let error s = raise (Usage s)
let commands = ["provision";"preflight";"run";"postcheck";"collect";"report";"archive";
  "execute";"stages";"finalize";"finalization-inspect";"checkpoint-inspect";"observation";"inspect";"preparation-inspect";"resources-inspect";"resource-inspect";"timing-intent-inspect";"compare";"workload-inspect"]
let tools = ["toolchain";"support-tools";"pdk-root"]
let identity = ["dependency-lock";"waiver"]
let grammar command query = match command with
  | "provision" -> ["toolchain";"python"], ["check";"offline";"no-container"], ""
  | "preflight" -> tools @ identity @ ["python";"results-root";"output"], ["native"], "bundle"
  | "run" -> tools @ identity @ ["python";"results-root";"runs";"stage"], ["native"], "bundle"
  | "execute" -> tools @ identity @ ["python";"results-root";"runs";"stage";"precheck-python";"testbench";"testbench-top";"archive"], ["native";"force"], "bundle"
  | "postcheck" -> tools @ identity @ ["precheck-python";"testbench";"testbench-top";"replay-trace";"timeout"], [], "run-dir"
  | "collect" -> ["output"], ["stdout"], "run-dir"
  | "report" -> [], [], "target"
  | "workload-inspect" -> [], [], "result"
  | "compare" -> ["left-kind";"left";"right-kind";"right";"axis";"left-workload";"right-workload"], [], ""
  | "resources-inspect" -> [], [], "run-dir"
  | "timing-intent-inspect" -> ["kind"], [], "target"
  | "resource-inspect" -> ["kind";"query";"resource-id";"section";"expected-digest";"artifact-id";"offset";"limit"], [], "target"
  | "inspect" -> ["domain";"stage";"row-id";"expected-digest"], [], "execution"
  | "archive" -> ["output"], ["list";"force"], "run-dir"
  | "stages" -> ["support-tools";"pdk-root";"python";"workspace";"checkpoint-store";"endpoint";"plan";"methodology-plan";"expected-plan-identity";"checkpoint";"timeout";"observation-store"], ["no-observation"], "bundle"
  | "finalize" -> ["operation-store";"support-tools";"precheck-python";"testbench";"replay-trace";"testbench-top"], ["accept"], "execution"
  | "finalization-inspect" -> [], [], "attempt"
  | "preparation-inspect" -> ["current-identity"], [], "attempt"
  | "checkpoint-inspect" -> ["compatibility";"restart-to"], [], "checkpoint"
  | "observation" -> (match query with
    | "describe" -> [], [], ""
    | "inspect" -> [], [], "attempt"
    | "read-updates" -> ["stream-id";"after"], [], "attempt"
    | "watch" -> ["stream-id";"after";"interval"], [], "attempt"
    | _ -> error "observation requires describe, inspect, read-updates or watch")
  | _ -> [], [], ""
let get a k = Hashtbl.find_opt a.values k
let value a k = match get a k with Some s -> s | None -> error ("required --"^k)
let flag a k = Hashtbl.mem a.flags k
let set a k v = Hashtbl.replace a.values k v
let expand choices key =
  if List.mem key choices then key else
  match List.filter (fun s -> String.starts_with ~prefix:key s) choices with
  | [s] -> s
  | [] -> error ("unrecognized argument: --"^key)
  | _ -> error ("ambiguous option: --"^key)
let help command query =
  Printf.printf "usage: hardcaml-asic-flow %s%s [options]\n" command (if command="report" then " TARGET" else if query="" then "" else " "^query);
  if command="" then (
    print_endline "Provision, validate, run, collect, report and archive TT/LibreLane work.";
    print_endline ("commands: " ^ String.concat ", " commands);
    print_endline "--version  package version and source-identity state")
  else (let values,flags,pos = grammar command query in
    if pos<>"" then Printf.printf "required argument: %s\n" (if command="report" then "TARGET" else pos);
    if command="report" then print_endline "TARGET: coarse run, stage checkpoint, stage workspace, or explicit execution JSON";
     if command="compare" then print_endline "Read-only pair comparison: explicit --left-kind/--left and --right-kind/--right, required --axis hardware|resource_policy|flow_configuration|descriptive. Optional --left-workload PATH and --right-workload PATH bind checked workload results to verified bundle/hardware identities. Whole JSON <=1MiB; no pagination. Exit0 is readability, not acceptance or a winner.";
     if command="inspect" then print_endline "Read-only retained evidence: --domain timing|clocks|physical|routing --stage synthesis|floorplan|placement|cts|routing|timing|signoff. --expected-digest alone pins a timing_analysis1 source; with --row-id it pins the index for detail. JSON contract: hardcaml-asic.inspection schema 1.";
     if command="preparation-inspect" then print_endline "Read-only retained Elaborate & Resolve Resources preparation. --current-identity compares an explicit preparation identity; absent comparison does not reconstruct current sources. JSON contract: hardcaml-asic.preparation-inspection schema 1.";
     if command="timing-intent-inspect" then print_endline "Read-only timing evidence. --kind execution requires an exact named execution JSON; --kind coarse_run selects ordinary linked synthesis evidence; default standalone preserves schema-1/2 audit inspection. Named evidence is path-bound; named archives/relocation unsupported. Exhaustive path-effect coverage remains unavailable.";
     if command="workload-inspect" then print_endline "Read-only public Workload_result.inspect of an explicit result.json. JSON <=1MiB. Exit0 means readable, including functional failure or partial evidence; exit2 means invalid evidence or usage.";
     List.iter (fun k -> Printf.printf "  --%s VALUE\n" k) values;
    List.iter (fun k -> Printf.printf "  --%s\n" k) flags);
  print_endline "-h, --help  show help\n--json  machine-readable payload (default: human summary)\n--verbose  expanded diagnostics on stderr";
  flush stdout
let parse argv =
  let values=Hashtbl.create 24 and flags=Hashtbl.create 8 in
  let rec global = function
    | ("--json"|"--verbose"|"--version" as x)::xs -> Hashtbl.replace flags (String.sub x 2 (String.length x-2)) true; global xs
    | ("--help"|"-h")::_ -> help "" ""; exit 0
    | s::xs when String.starts_with ~prefix:"--" s ->
      let k=expand ["version";"json";"verbose";"help"] (String.sub s 2 (String.length s-2)) in
      if k="help" then (help "" "";exit 0);
      Hashtbl.replace flags k true;global xs
    | xs -> xs in
  let rest=global argv in
  let command,rest=match rest with [] -> "",[] | c::xs when List.mem c commands -> c,xs
    | c::_ -> error ("invalid command: "^c) in
  let query,rest=if command="observation" then match rest with
    | ("--help"|"-h")::_ -> print_endline "usage: hardcaml-asic-flow observation {describe,inspect,read-updates,watch} [--json] [--verbose]"; exit 0
    | _ ->
      let rec before = function
        | ("--json"|"--verbose" as s)::xs -> Hashtbl.replace flags (String.sub s 2 (String.length s-2)) true; before xs
        | ("--help"|"-h")::_ -> print_endline "usage: hardcaml-asic-flow observation {describe,inspect,read-updates,watch} [--json] [--verbose]"; exit 0
        | q::xs -> q,xs | [] -> error "observation query is required" in before rest
    else "",rest in
  let a={command;query;values;flags;waivers=[]} in
  let options,switches,pos=grammar command query in
  let rec local positional = function
    | [] -> ()
    | ("--help"|"-h")::_ -> help command query; exit 0
    | "--"::xs -> List.iter (fun s -> positional s) xs
    | s::xs when String.starts_with ~prefix:"--" s ->
      let key,inline=match String.index_opt s '=' with
        | None -> String.sub s 2 (String.length s-2),None
        | Some i -> String.sub s 2 (i-2),Some (String.sub s (i+1) (String.length s-i-1)) in
      let key=expand (options @ switches @ ["help"] @ (if command="provision" then [] else ["json";"verbose"])) key in
      if key="help" && inline=None then (help command query;exit 0);
      if List.mem key (switches @ (if command="provision" then [] else ["json";"verbose"])) && inline=None
      then (Hashtbl.replace flags key true; local positional xs)
      else if List.mem key options then (
        let argument v = v="-" || not (String.starts_with ~prefix:"-" v) ||
          (String.length v>1 && (match v.[1] with '0'..'9' | '.' -> true | _ -> false)) in
        let v,xs=match inline,xs with Some v,_ -> v,xs | None,v::xs when argument v -> v,xs
          | _ -> error ("--"^key^" requires an argument") in
        if command="compare" && Hashtbl.mem a.values key then error ("duplicate --"^key);
        if key="waiver" then a.waivers<-a.waivers@[v] else set a key v;
        local positional xs)
      else error ("unrecognized argument: "^s)
    | s::_ when String.starts_with ~prefix:"-" s && s<>"-" -> error ("unrecognized argument: "^s)
    | s::xs -> positional s; local positional xs in
  local (fun s -> if pos="" || get a pos<>None then error ("unrecognized argument: "^s) else set a pos s) rest;
  if pos<>"" && get a pos=None then error ("required argument: "^pos);
  a
