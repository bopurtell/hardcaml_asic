(* University of Florida *)
(* Author: Bohdan Purtell *)
(* Module: "elaboration/context.ml" *)
(* Source implementation of the elaboration context system. These are essentially
   databases that are spawned for an individual project elaboration;
*)

open! Core
open! Hardcaml

[@@@ocamlformat "disable"]
(* A context starts, and ends up either Finalized or Failed;
   If one wants to be more mathematically pedantic we could technically make a set on the final state between SUCCESS and ERROR but this is fine for a tool like this.
*)
module State = struct
  type t =
    | Open
    | Finalized
    | Failed
  [@@deriving sexp_of]
end

(* Biggest ownership items for the context.

  The mode in which we're operating,
  The technology we're using and its features,
  The resource mapping policy,
  The Circuit_database.t that Hardcaml uses that we may draw from inside of,
  The *mutable* State of the context,
  The *mutable* result of the Resource Mapping the tool ultimately did.
      Very important!
*)
type shared =
  { mode            : Mode.t
  ; technology      : Resource.Technology.t
  ; policy          : Resource.Policy.t
  ; database        : Circuit_database.t
  ; mutable state   : State.t
  ; mutable records : Resource.Record.t Map.M(Resource.Id).t
  ; mutable macro_counts : int Map.M(String).t
  ; mutable timing_stages : (Platform.Timing.Stage.t * Signal.t) list
  }

(* Elaboration.Context.t : maintains a shared grouping record, as well as the main scope it's attached to in the hierarchy; *)
type t =
  { shared  : shared
  ; scope   : Scope.t
  }

let check_open
  t
  ~operation
  =

  (* take in an Elaboration.Context.t object; match the state with things;
      if open then chilling
      if not, then raise on it
  *)
  match t.shared.state with
  | Open -> ()
  | (Finalized | Failed) as state ->
    raise_s
      [%message
        "elaboration context is closed; each elaboration uses a fresh context"
          (operation : string)
          (state : State.t)]
;;

(* Close the context as Failed. Shared by every view, since the state lives in [shared]. *)
let fail t = t.shared.state <- Failed

(* grab the mode out of a context object *)
let mode t = t.shared.mode

(* grab the scope only if open *)
let scope t =
  check_open t ~operation:"scope";
  t.scope
;;

(* lets us grab a new view into an existing elaboration context; grab a scope and turn it into a context attached; *)
let in_scope t scope =
  check_open t ~operation:"in_scope";
  if not (phys_equal (Scope.circuit_database scope) t.shared.database)
  then raise_s [%message "scope does not belong to this elaboration"];
  { t with scope }
;;

(* Takes in an elaboration mode,
an elaboration,
a request to do something,
and a selection and returns the resource map enumeration tuple list;

Once an implementation is picked, decides two things for the Resource.Record that is output:
   Elaborated_as.t  :  what this elaboration actually built for the resource.
   Source.t list    :  artifacts downstream steps (simulation, synthesis, hardening) need for it.
*)
let sources_of
    ~(mode : Mode.t)
    ~(technology : Resource.Technology.t) (* description of what the company can do; list of mappings between requests and macros; *)
    (request : Resource.Request.t) (* what is the resource asking for? kind plus a contract, not dependent on a specific technology *)
    (selection : Resource.Selection.t) (* This is a request, tech, and req wrapped up; *)
  : Resource.Record.Elaborated_as.t * Resource.Record.Source.t list
  =

  (* do the thing with the stuff *)
  (* A selection is a record of a choice and the why it was made; *)
  match (mode : Mode.t),
        (selection : Resource.Selection.t).implementation
  with

  (* sim generation; only care about behaviour *)
  (* Selection is ignored; simulation always uses the behavioural model, even if a macro was selected;
      The Behavioral_model tag records that the hardware we simulated is not the implementation that was selected.
  *)
  | Simulation, _ -> Behavioral_model, [ Generated_behavioral_model ]

  (* Implementing with flops; *)
  (* Resource becomes arbitrary RTL from the Hardcaml design and gets handed to synthesis; *)
  | Implementation, Flops -> Selected_implementation, [ Generated_synthesis_rtl ]

  (* Implementation with a set macro of something; *)
  (* The resource is a hard macro, so the files that come with it matter; function looks up mapping again and turns each entry
     into a Source.Collateral -> see expect tests
  *)
  | Implementation, Macro _ ->
    let collateral =
      match Resource.Technology.exact_mapping technology request with
      | Some { macro; _ } -> macro.collateral
      | None ->
        raise_s
          [%message
            "selected macro has no exact technology mapping; register and select from the same technology"
              (request : Resource.Request.t)]
      (* TODO (possible optimization): this second lookup could go away; see
         Selection.Implementation *)
    in

    ( Selected_implementation
    , List.map collateral ~f:(fun c -> Resource.Record.Source.Collateral c)
    )
;;

(* The main sauce here for contexts;
   pass it an elaboration context, a named string, and a Resource.Request.t

    _exn denotes that this function may raise an exception instead of returning an error value; Jane convention.

   This is the body of a registration on a context already known to be open; every raise in
   here is caught by [register_exn] below, which closes the context before re-raising.
*)
let register_open_exn
    t
    ~name
    request =

  (* grab the path of the scope of the context, feed it to a list transform, and then reverse it to get the outermost-first order of the path. *)
  (* The identity of the registration item; *)
  let path = Scope.path t.scope |> Scope.Path.to_list |> List.rev in (* order of to_list might matter eventually *)
  let id = Resource.Id.create_exn ~path ~name in

  (* grabbing the resource selection record map; *)
  (* Reject duplicates; this raises with a ppx for an sexp on the raise call for context on the occurrence. *)
  (* Can be serialized; *)
  if Map.mem t.shared.records id
  then
    raise_s
      [%message
        "duplicate resource instance; give each instance in a scope a distinct name"
          ~instance:(id : Resource.Id.t)
          (request : Resource.Request.t)];

  (* extract the items out of the shared context; *)
  let { mode
      ; technology
      ; policy
      ; _
      } = t.shared in

  let record =
    (* If no policy rule applies, then stop and return the error; short circuits in a monad; *)
    let%bind.Or_error requirement, applied = Resource.Policy.lookup policy id in

    (* Wraps in Ok if it passes; *)
    let exhausted_limit macro =
      Option.filter (Resource.Policy.macro_limit policy macro) ~f:(fun limit ->
        Option.value (Map.find t.shared.macro_counts macro) ~default:0 >= limit)
    in
    let%map.Or_error selection =
      Resource.Selection.select_with_budget ~exhausted_limit ~technology ~requirement request
    in

    (* What the elaboration built for the resource actually; *)
    let elaborated_as, sources = sources_of
        ~mode ~technology request selection
    in

    { Resource.Record.id
    ; request
    ; requirement
    ; policy = applied
    ; selection
    ; elaborated_as
    ; sources
    }

  in

  (* Very painful to do while inside of a design constructor for the Hardcaml circuit, which returns the Signal.t O.t
      If the register_exn returned Or_error.t, every RAM constructor and hierarchy level would have to pass the Or_error upwards;

    With this, we can convert the error into an exception to become a value again;
  *)
  match record with
  | Error error ->
    Error.raise
      (Error.tag_s
         error
         ~tag:
           [%message
             "resource selection failed"
               ~instance:(id : Resource.Id.t)
               (request : Resource.Request.t)])
  | Ok record ->
    t.shared.records <- Map.set t.shared.records ~key:id ~data:record;
    (match record.selection.implementation with
     | Macro macro when Option.is_some (Resource.Policy.macro_limit policy macro) ->
       t.shared.macro_counts <- Map.update t.shared.macro_counts macro
         ~f:(fun count -> Option.value count ~default:0 + 1)
     | Macro _ | Flops -> ());
    record
;;

(* Any failed registration closes the context as Failed before the exception leaves

   A design is free to catch the exception (try ... with _ -> ...), and Project.elaborate would
   then never see it; closing here means the swallowed failure still stops the elaboration; any
   later registration raises, and Private.finalize returns an error instead of a project that is
   missing the resource. I think there was a possible race condition here but I dunno.

   check_open stays outside the handler: a closed context is already closed, and a Finalized
   one must not be rewritten to Failed. The original exception and backtrace are re-raised
   unchanged.
*)
let register_exn t ~name request =
  check_open t ~operation:"register";
  try register_open_exn t ~name request with
  | exn ->
    let backtrace = Backtrace.Exn.most_recent () in
    fail t;
    Exn.raise_with_original_backtrace exn backtrace
;;

(* This is the private construction mechanism by which we align call conventions to;
    In hardcaml, things aren't truly "private" per se, but as a Jane convention we use this and enforce it;

  Because of how MLIs work we can somewhat emulate this item as the "only" way to make Elaboration.Context.t

*)
let register_timing_stage_exn t ~stage ~q =
  check_open t ~operation:"register_timing_stage";
  try
    if Signal.width q <> 1 || not (Signal.Type.is_reg q)
    then invalid_arg "timing stage requires an existing scalar register Q";
    if List.exists t.shared.timing_stages ~f:(fun (s, other) ->
      String.equal s.Platform.Timing.Stage.id stage.Platform.Timing.Stage.id
      || Signal.Type.Uid.equal (Signal.uid q) (Signal.uid other))
    then invalid_arg "timing stage ID or Q registered more than once";
    let anchor = Platform.Timing.Stage.anchor stage in
    if List.exists (Signal.names q) ~f:(String.is_prefix ~prefix:"hc_pt7_")
    then invalid_arg "timing register already has a reserved hc_pt7_ name";
    if List.exists (Signal.attributes q) ~f:(fun a ->
      String.equal (Rtl_attribute.name a) "keep")
    then invalid_arg "timing register keep attribute is owned by registration";
    ignore (Signal.( -- ) q anchor : Signal.t);
    ignore (Signal.add_attribute q
      (Rtl_attribute.create ~value:(Int 1) "keep") : Signal.t);
    t.shared.timing_stages <- (stage, q) :: t.shared.timing_stages
  with exn -> fail t; raise exn
;;

module Private = struct
  let create ~mode ~technology ~policy ~scope =
    { shared =
        { mode
        ; technology
        ; policy
        ; database = Scope.circuit_database scope (* derives from the hardcaml scope's circuit database *)
        ; state = Open
        ; records = Map.empty (module Resource.Id)
        ; macro_counts = Map.empty (module String)
        ; timing_stages = []
        }
    ; scope
    }
  ;;

  let finalize t =
    (* A Failed context here means a registration raised and the design caught it; construction
       "succeeded", so report it as an error rather than raising out of Project.elaborate.
    *)
    match t.shared.state with
    | Failed -> (* see the failed and prop the sexp exception conversion; *)
      Or_error.error_s
        [%message
          "a resource registration failed during construction and the exception was caught \
           inside the design"]

    (* If open, then recheck and convert to finalizing; *)
    | Open | Finalized ->
      check_open t ~operation:"finalize";
      let records = Map.data t.shared.records in

      (* The selectors that actually decided some resource's requirement; selectors are unique
         within a policy, so a selector here identifies exactly one rule;
      *)
      let applied =
        List.filter_map records ~f:(fun record ->
          match record.policy with
          | Rule selector -> Some selector
          | Default -> None)
      in

      (* A rule is dead when it never applied. Matching is not enough: a rule can match
         resources and still lose to a higher-precedence rule on every one of them;

         unmatched : matches no registered resource; most likely a misspelt identity;
         shadowed  : matches resources, but each of them was decided by a more specific
                     rule, listed as overridden_by; the two rules contradict or repeat;
      *)
      let dead_rules =
        List.filter (Resource.Policy.rules t.shared.policy) ~f:(fun rule ->
          not (List.mem applied rule.selector ~equal:Resource.Policy.Selector.equal))
      in
      let unmatched_rules, shadowed_rules =
        List.partition_map dead_rules ~f:(fun rule ->
          match
            List.filter records ~f:(fun record ->
              Resource.Policy.Selector.matches rule.selector record.id)
          with
          | [] -> First rule
          | matched ->
            let overridden_by =
              List.filter_map matched ~f:(fun record ->
                match record.policy with
                | Rule selector -> Some selector
                | Default -> None) (* unreachable; a matching rule always beats the default *)
              |> List.dedup_and_sort ~compare:Resource.Policy.Selector.compare
            in
            Second
              [%message
                ""
                  (rule : Resource.Policy.Rule.t)
                  (overridden_by : Resource.Policy.Selector.t list)])
      in

      match dead_rules with
      | [] ->
        t.shared.state <- Finalized;
        Ok records
      | _ :: _ ->
        t.shared.state <- Failed;
        Or_error.error_s
          [%message
            "implementation policy rules never applied to a registered resource"
              (unmatched_rules : Resource.Policy.Rule.t list [@sexp.omit_nil])
              (shadowed_rules : Sexp.t list [@sexp.omit_nil])
              ~registered:(List.map records ~f:(fun r -> r.id) : Resource.Id.t list)]
  ;;

  (* if the fail happens, here it is! *)
  let fail = fail
  let timing_stages t = t.shared.timing_stages
end
[@@@ocamlformat "enable"]
