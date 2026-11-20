(* University of Florida *)
(* Author: Bohdan Purtell *)
(* Module: "resource/policy.ml" *)
(* Source implementation of the project resource policy. A policy is the project's answer
   to "what am I willing to accept for this resource?";

   It is a default requirement plus a list of rules, where each rule points at one
   instance or at a whole scope subtree. Elaboration.Context.register_exn asks it for a
   requirement through [lookup], and Private.finalize walks its [rules] to catch rules
   that never applied (matched nothing, or were always outranked);

   It does NOT know what the technology can do, that is Technology's job; Selection is
   where the two meet.
*)

open! Core

[@@@ocamlformat "disable"]

(* What the project accepts for a resource;

   Flops                  : always build it out of flops, even if a macro exists;
   Exact                  : must be an exact technology macro, otherwise registration fails;
   Exact_or_flop_fallback : take the exact macro when there is one, otherwise flops,
                            and the selection records it as a fallback so it is never silent;
*)
module Requirement = struct
  type t =
    | Flops
    | Exact
    | Exact_or_flop_fallback
  [@@deriving compare, equal, sexp_of]
end

(* Which resources a rule applies to;

   Instance : exactly one resource, by its full identity (path plus name);
   Subtree  : every resource registered in that scope path or anywhere below it;

   The type is private in the mli, so the only way to build one is through the _exn
   constructors below, which means every selector has already been validated.

   We importantly store WHY we got its requirement, not just what it happened to get.
   For performance optimizations, it might make sense to turn this off.
*)
module Selector = struct
  type t =
    | Instance of Id.t
    | Subtree of string list
  [@@deriving compare, equal, sexp_of]

  (* "a/b/name" -> Instance; Id does the parsing and validation, so an instance
     selector follows the exact same identifier rules as a registered resource; *)
  let instance_exn s = Instance (Id.of_string_exn s)

  (* "a/b" -> Subtree ["a"; "b"];
     split on '/' and check each piece is a valid identifier; an empty piece shows up for
     "a//b", "a/" or "" so all of those get rejected here;

     A consequence: there is no subtree selector for the top scope itself ("" is invalid);
     the default is what covers everything.
  *)
  let subtree_exn s =
    let path = String.split s ~on:'/' in
    if not (List.for_all path ~f:Id.is_valid_component)
    then
      raise_s
        [%message
          "subtree selector must be a nonempty '/'-separated scope path" ~selector:s];
    Subtree path
  ;;

  (* Does this selector apply to the resource [id]?

     Instance : identities must be equal;
     Subtree  : the selector's path must be a prefix of the resource's scope PATH;

     Careful: Subtree only ever looks at [id.path], never [id.name]. The resource
     "left/buf" has path ["left"] and name "buf", so subtree "left" matches it but subtree
     "left/buf" does not. That mistake is not silent though; the rule matches nothing and
     finalize reports it.
  *)
  let matches t (id : Id.t) =
    match t with
    | Instance selected -> Id.equal selected id
    | Subtree prefix -> List.is_prefix id.path ~prefix ~equal:String.equal
  ;;

  (* Ranking used by [lookup] when more than one rule matches; compared as a tuple, so the
     first number decides and the second only breaks ties;

     Instance -> (2, 0)             : an instance rule beats any subtree rule;
     Subtree  -> (1, depth of path) : between subtrees, the deeper (more specific) one wins;

     The 0 on Instance never matters: at most one instance rule can match a given id.
  *)
  let precedence = function
    | Instance _ -> 2, 0
    | Subtree prefix -> 1, List.length prefix
  ;;
end

(* One entry in the policy; "resources picked by [selector] get [requirement]"; *)
module Rule = struct
  type t =
    { selector : Selector.t
    ; requirement : Requirement.t
    }
  [@@deriving sexp_of]
end

(* Which part of the policy produced a requirement; this ends up in
   Record.policy, so the inventory shows WHY a resource got its requirement, not
   just what it got;

   Default : no rule matched, the default was used;
   Rule s  : the rule with selector [s] won;
*)
module Applied = struct
  type t =
    | Default
    | Rule of Selector.t
  [@@deriving compare, equal, sexp_of]
end

(* The policy itself;

   default is optional on purpose: with no default, a resource that no rule covers fails
   at registration instead of quietly getting some implementation nobody asked for;
*)
type t =
  { default : Requirement.t option
  ; rules : Rule.t list
  ; macro_limits : int Map.M(String).t [@sexp.omit_if Map.is_empty]
  }
[@@deriving sexp_of]

(* Build a policy; returns Or_error rather than raising since this runs when the project
   is declared, well before any design constructor, so there is nothing to thread it
   through;

   The same selector cannot appear in two rules. This is rejected even if
   both rules ask for the same requirement, since two rules with the same selector always
   tie on precedence and [lookup] would have no way to pick between them;


    Request     : what is being asked for by a resource constructor?
    Requirement : what will the project accept - derived from a policy
    Tech Map    : what macros exist, based on the request for exact equality
*)
let create ?default ?(macro_limits = []) rules =
  match
    List.find_a_dup rules ~compare:(fun (a : Rule.t) b ->
      Selector.compare a.selector b.selector)
  with
  | Some { selector; _ } ->
    Or_error.error_s
      [%message
        "ambiguous implementation policy: selector appears in more than one rule"
          (selector : Selector.t)]
  | None ->
    let%map.Or_error macro_limits =
      List.fold_result macro_limits ~init:(Map.empty (module String))
        ~f:(fun limits (macro, limit) ->
          if String.is_empty (String.strip macro) || limit < 0
          then Or_error.error_s [%message "expected a macro name and a nonnegative instance limit"
                                           (macro : string) (limit : int)]
          else if Map.mem limits macro
          then Or_error.error_s [%message "duplicate macro instance limit" (macro : string)]
          else Ok (Map.set limits ~key:macro ~data:limit))
    in
    { default; rules; macro_limits }
;;

let macro_limit t macro = Map.find t.macro_limits macro
let limited_macro_names t = Map.keys t.macro_limits

(* grab the rules out of a policy; Private.finalize uses this for its unused rule check *)
let rules t = t.rules

(* The main sauce here for policies; given a resource identity, return its requirement and
   which part of the policy chose it;

   1. keep every rule whose selector matches [id];
   2. take the one with the highest precedence (instance, then deepest subtree);
   3. nothing matched -> fall back to the default;
   4. no default either -> error; register_exn tags it with the request and raises;
*)
let lookup
  t (* Resource.Policy.t *)
  (id : Id.t)
  =
  (* Equal precedence implies an identical selector, which [create] rejects, so the
     maximum is unique. *)
  (* Why: two matching instance rules would both name [id], and two matching subtree rules
     of the same depth are both prefixes of the same path with the same length, so they
     are the same path; either way it is a duplicate selector; *)
  let matching =
    List.filter t.rules ~f:(fun rule ->
      Selector.matches rule.selector id
    )
  in

  match
    List.max_elt matching ~compare:(fun (a : Rule.t) b ->
      [%compare: int * int]
        (Selector.precedence a.selector)
        (Selector.precedence b.selector))
  with

  (* a rule won; record the winning selector as the reason *)
  | Some { selector; requirement } -> Ok (requirement, Applied.Rule selector)
  | None ->
    (match t.default with
     | Some requirement -> Ok (requirement, Applied.Default)
     | None ->
       Or_error.error_s
         [%message
           "no implementation policy applies; add a default or a rule selecting this \
            instance"
             ~instance:(id : Id.t)])
;;
[@@@ocamlformat "enable"]
