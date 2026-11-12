(* Shared coarse/segmented macro preflight. PDK I/O belongs here, never in
   elaboration. Retain declared aliases as well as resolved content identities. *)
module J = Identity_json
let checked = function Ok x -> x | Error e -> failwith e
let codec = function Ok x -> x | Error e -> failwith (J.error_message e)
let get = Macro_contract.get
let text = Macro_contract.text
let array = Macro_contract.array
let s = Macro_contract.s
let obj = Macro_contract.obj
let hash p = (checked (Checkpoint.file_record p)).sha256
let read p = codec (Result_store.read_json p)
let safe root relative =
  if relative="" || not (Filename.is_relative relative) ||
     List.exists (fun x -> x="" || x="." || x="..") (String.split_on_char '/' relative)
  then invalid_arg "unsafe macro input reference";
  let path=Unix.realpath (root ^ "/" ^ relative) in
  if not (String.starts_with ~prefix:(Unix.realpath root ^ "/") path) then
    invalid_arg "macro input escapes pinned PDK";
  path
let collect ~manifest ~bundle ~pdk_root ~command ~check_cancel =
  let collateral=Macro_contract.validate manifest (read (bundle ^ "/src/config.json")) in
  let technology=text (get (get manifest "target") "technology") in
  let refs=List.map (fun (role,reference) ->
    check_cancel ();
    let relative=technology ^ "/" ^ String.sub reference 9 (String.length reference-9) in
    let path=try safe pdk_root relative with
      | Unix.Unix_error (Unix.ENOENT,_,_) -> invalid_arg ("missing macro " ^ role ^ ": " ^ pdk_root ^ "/" ^ relative) in
    let rec aliases base = function
      | [] -> []
      | name::rest -> let next=base ^ "/" ^ name in
        (if (Unix.lstat next).Unix.st_kind=Unix.S_LNK then [next] else []) @ aliases next rest in
    let aliases=aliases pdk_root (String.split_on_char '/' relative) in
    if command "git" (["-C";pdk_root;"status";"--porcelain";"--";path] @ aliases) <> ""
    then invalid_arg ("modified macro " ^ role ^ ": " ^ path);
    obj ["role",s ("macro:" ^ role);"reference",s reference;"path",s path;"sha256",s (hash path);
      "aliases",J.array (List.map (fun path -> obj ["path",s path;"target",s (Unix.readlink path)]) aliases)]) collateral in
  if not (Macro_contract.physical manifest) then refs else (
    let plan=get (get manifest "macro_contract") "physical_plan" in
    if read (bundle ^ "/physical/macro-plan.json") <> plan then invalid_arg "physical plan file differs from manifest";
    List.iter (fun (relative,contents) ->
      if hash (bundle ^ "/" ^ relative) <> J.sha256_bytes contents then
        invalid_arg ("physical input differs from canonical generation: " ^ relative)) (Macro_contract.physical_files manifest);
    let rec tree ancestors path =
      check_cancel ();
      let path=Unix.realpath path in
      if not (String.starts_with ~prefix:(Unix.realpath pdk_root ^ "/") path) || List.mem path ancestors
      then invalid_arg "checking collateral escape or cycle";
      match (Unix.stat path).Unix.st_kind with
      | Unix.S_DIR -> Array.to_list (Sys.readdir path) |> List.sort String.compare |>
        List.concat_map (fun n -> tree (path::ancestors) (path ^ "/" ^ n))
      | Unix.S_REG -> [path]
      | _ -> invalid_arg "unsupported checking collateral" in
    let paths=List.concat_map (fun relative -> tree [] (safe pdk_root relative))
      ["ihp-sg13cmos5l/libs.tech/klayout";"ihp-sg13g2/libs.tech/klayout/tech/drc";
       "ihp-sg13cmos5l/libs.tech/magic";"ihp-sg13cmos5l/libs.tech/netgen"] in
    let paths=List.sort_uniq String.compare (paths @ List.map (safe pdk_root)
      ["ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/verilog/sg13cmos5l_stdcell.v";
       "ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/verilog/sg13cmos5l_udp.v"]) in
    if command "git" (["-C";pdk_root;"status";"--porcelain";"--"] @ paths) <> "" then
      invalid_arg "modified pinned checking collateral";
    refs @ List.map (fun path -> obj ["role",s "physical:checking_collateral";"path",s path;"sha256",s (hash path)]) paths)
let verify ?(phase="") ~manifest ~pdk_root references =
  let changed what = what ^ " changed" ^ (if phase="" then "" else " " ^ phase) in
  List.iter (fun r ->
    let path=text (get r "path") in
    if hash path <> text (get r "sha256") then invalid_arg (changed "macro/target collateral" ^ ": " ^ path);
    if String.starts_with ~prefix:"macro:" (text (get r "role")) then (
      (match get r "aliases" with J.Null -> () | aliases ->
        List.iter (fun a -> if Unix.readlink (text (get a "path")) <> text (get a "target") then
          invalid_arg (changed "macro collateral alias bytes")) (array aliases));
      let reference=text (get r "reference") in
      let relative=text (get (get manifest "target") "technology") ^ "/" ^ String.sub reference 9 (String.length reference-9) in
      if safe pdk_root relative <> path then invalid_arg (changed "macro collateral alias" ^ ": " ^ reference))) references
