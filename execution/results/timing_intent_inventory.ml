let max_sources = 4096
let max_source_bytes = 128 * 1024 * 1024
let max_total_bytes = 512 * 1024 * 1024
type usage = { source_count : int; total_bytes : int; largest_bytes : int }
type alias = { path:string; target:string; root:string; sha256:string; bytes:int }
let require b message = if not b then invalid_arg ("Named timing inventory: "^message)
let absolute path =
  require (not (Filename.is_relative path) && not (String.contains path '\000')) "absolute source path required";
  (match Checkpoint.absolute_path path with Ok p -> require (p=path) "non-normalized declared source path" | Error e -> invalid_arg e)
let within root path = String.starts_with ~prefix:(root^"/") path
let canonical_root root =
  absolute root;
  require (root<>"/" && Unix.realpath root=root && (Unix.lstat root).Unix.st_kind=Unix.S_DIR) "canonical collateral root required"
let strict_regular path =
  absolute path;
  require (Unix.realpath path=path) "linked/noncanonical source";
  let stat=Unix.lstat path in
  require (stat.Unix.st_kind=Unix.S_REG) "source is not regular";
  require (stat.st_size<=max_source_bytes) "source exceeds 128MiB";
  stat
let external_resolution ~roots path =
  absolute path;
  let target=Unix.realpath path in
  let matching=List.filter (fun root -> within root path) roots in
  let root=match matching with
    | [root] -> root
    | [] -> invalid_arg "Named timing inventory: alias outside allowed collateral root"
    | _ -> invalid_arg "Named timing inventory: ambiguous collateral root" in
  canonical_root root;
  require (within root target) "alias target outside allowed canonical root";
  target,root,strict_regular target
let regular ?(aliases=[]) path =
  match List.filter (fun a -> a.path=path) aliases with
  | [] -> strict_regular path
  | [a] ->
    let target,root,stat=external_resolution ~roots:[a.root] path in
    require (a.path<>a.target && target=a.target && root=a.root) "declared alias resolution differs from bound target";
    require (stat.st_size=a.bytes) "declared alias byte count differs";
    stat
  | _ -> invalid_arg "Named timing inventory: duplicate alias declaration"
let admit_paths ?(aliases=[]) ?(external_roots=[]) paths =
  require (List.length paths<=max_sources) "source count exceeds 4096";
  require (List.length paths=List.length (List.sort_uniq String.compare paths)) "duplicate named source path";
  List.fold_left (fun usage path ->
    let stat=if external_roots=[] || List.exists (fun a -> a.path=path) aliases || Unix.realpath path=path then regular ~aliases path
      else let _,_,stat=external_resolution ~roots:external_roots path in stat in
    require (usage.total_bytes<=max_total_bytes-stat.st_size) "sources exceed 512MiB";
    {source_count=usage.source_count+1;total_bytes=usage.total_bytes+stat.st_size;
     largest_bytes=max usage.largest_bytes stat.st_size})
    {source_count=0;total_bytes=0;largest_bytes=0} paths
let same a b =
  a.Unix.st_dev=b.Unix.st_dev && a.st_ino=b.st_ino && a.st_kind=b.st_kind &&
  a.st_size=b.st_size && a.st_mtime=b.st_mtime && a.st_ctime=b.st_ctime
let hash ?(aliases=[]) path =
  let admitted=regular ~aliases path in
  let fd=Unix.openfile path [Unix.O_RDONLY;Unix.O_NONBLOCK] 0 in
  let ic=Unix.in_channel_of_descr fd in
  Fun.protect ~finally:(fun () -> close_in_noerr ic) (fun () ->
    let before=Unix.fstat fd in
    require (same admitted before) "source changed before hashing";
    let digest=Cryptokit.hash_channel (Cryptokit.Hash.sha256 ()) ~len:before.st_size ic
      |> Cryptokit.transform_string (Cryptokit.Hexa.encode ()) in
    require (same before (Unix.fstat fd) && same before (regular ~aliases path)) "source changed while hashing";
    List.iter (fun a -> if a.path=path && a.sha256<>"" then require (digest=a.sha256) "declared alias hash differs") aliases;
    digest)
let capture_aliases ~roots ~paths =
  ignore (admit_paths ~external_roots:roots paths);
  List.filter_map (fun path ->
    if Unix.realpath path=path then None else (
      let target,root,stat=external_resolution ~roots path in
      let provisional={path;target;root;sha256="";bytes=stat.st_size} in
      let sha256=hash ~aliases:[provisional] path in Some {provisional with sha256})) paths
let aliases_to_json aliases = `List (List.map (fun a -> `Assoc [
  "path",`String a.path;"target",`String a.target;"root",`String a.root;
  "sha256",`String a.sha256;"bytes",`Int a.bytes]) aliases)
let aliases_of_json = function
  | `List rows ->
    require (List.length rows<=max_sources) "alias count exceeds 4096";
    let aliases=List.map (function
      | `Assoc fields ->
        require (List.sort String.compare (List.map fst fields)=List.sort String.compare ["path";"target";"root";"sha256";"bytes"])
          "unexpected alias fields";
        let text key=match List.assoc key fields with `String s when s<>"" -> s | _ -> invalid_arg "invalid alias string" in
        let path=text "path" and target=text "target" and root=text "root" and sha256=text "sha256" in
        List.iter absolute [path;target;root];
        require (String.length sha256=64 && String.for_all (function '0'..'9'|'a'..'f'->true | _->false) sha256) "invalid alias SHA256";
        let bytes=match List.assoc "bytes" fields with `Int n when n>=0 && n<=max_source_bytes -> n | _ -> invalid_arg "invalid alias byte count" in
        {path;target;root;sha256;bytes}
      | _ -> invalid_arg "alias row object required") rows in
    let paths=List.map (fun a->a.path) aliases in
    require (List.length paths=List.length (List.sort_uniq String.compare paths)) "duplicate alias declaration";
    aliases
  | _ -> invalid_arg "external_aliases array required"
let validate_aliases ~roots ~external_files aliases =
  require (List.length aliases<=max_sources) "alias count exceeds 4096";
  let declared=List.map (fun a->a.path) aliases in
  require (List.length declared=List.length (List.sort_uniq String.compare declared)) "duplicate alias declaration";
  List.iter (fun a ->
    require (List.mem a.root roots) "alias root differs from recorded collateral roots";
    (match List.assoc_opt a.path external_files with
     | Some expected -> require (expected.Checkpoint.sha256=a.sha256 && expected.bytes=string_of_int a.bytes)
         "alias hash/bytes differ from exact external closure key"
     | None -> invalid_arg "Named timing inventory: alias is not an exact external closure key");
    ignore (regular ~aliases:[a] a.path)) aliases;
  List.iter (fun (path,_) -> if not (String.starts_with ~prefix:"/nix/store/" path) && Unix.realpath path<>path then
    require (List.exists (fun a->a.path=path) aliases) "external alias missing bound association") external_files
let admit_checkpoint directory =
  let path=directory^"/manifest.json" in
  let admitted=regular path in
  let fd=Unix.openfile path [Unix.O_RDONLY;Unix.O_NONBLOCK] 0 in
  let ic=Unix.in_channel_of_descr fd in
  let raw=Fun.protect ~finally:(fun () -> close_in_noerr ic) (fun () ->
    let before=Unix.fstat fd in
    require (same admitted before) "checkpoint metadata changed before reading";
    require (before.st_size<=8*1024*1024) "checkpoint metadata exceeds 8MiB";
    let bytes=really_input_string ic before.st_size in
    require (same before (Unix.fstat fd) && same before (regular path)) "checkpoint metadata changed while reading";
    bytes) in
  let document=match Evidence_json.parse ~reject_duplicates:true raw with Ok j -> j | Error e -> invalid_arg e in
  let get j key=match j with `Assoc xs -> Option.value (List.assoc_opt key xs) ~default:`Null | _ -> `Null in
  let fields=function `Assoc xs -> xs | _ -> invalid_arg "named checkpoint inventory object required" in
  let raw=Evidence_json.value document in
  let files=fields (get raw "files") |> List.map (fun (name,_) ->
    require (Filename.is_relative name && List.for_all (fun x -> x<>"" && x<>"." && x<>"..") (String.split_on_char '/' name))
      "unsafe checkpoint inventory path";
    directory^"/"^name) in
  let external_=fields (get raw "external") |> List.filter_map (fun (p,_) ->
    if String.starts_with ~prefix:"/nix/store/" p then None else Some p) in
  ignore (admit_paths (path::files));
  let compatibility=get raw "compatibility" in
  let roots=List.filter_map (function `String s when s<>"" -> Some s | _ -> None)
    [get compatibility "pdk_root";(match get compatibility "project" with `String p -> `String (p^"/tt") | _ -> `Null)] in
  ignore (admit_paths ~external_roots:roots (List.sort_uniq String.compare (path::files@external_)))
