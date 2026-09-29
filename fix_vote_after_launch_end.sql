-- QA-031: toggleProductVote checked only for future launches (launch_date > NOW()), so a
-- signed-in user could still add or remove their own vote AFTER the launch window ended
-- (launch_end). The browser blocks that ("Voting has ended"); the server did not.
-- Impact: votes_count, weekly_winners (no time filter) and the winner-email ordering
-- (get_prev_launch_weeks sorts by votes_count) react immediately, so closed weeks —
-- including the winner emails, when the bump lands before the cron runs — can be
-- rewritten after the fact (one vote per account, both directions). The badge chain
-- (winner_of_the_day/week/month) filters votes by created_at and is therefore only
-- affected by vote REMOVAL, not by addition.
-- Fix: reject the call once the launch window has closed. Same ERRCODE as the caller
-- check above, so the app treats it like the other rejections.
-- Regression test: devhunt-poc/poc_vote_after_launch_end.py (exit code 2 = patched).

CREATE OR REPLACE FUNCTION public."toggleProductVote"(_product_id bigint, _user_id uuid)
 RETURNS integer
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path = public
AS $function$BEGIN
  IF auth.uid() IS NULL OR auth.uid() <> _user_id THEN
    RAISE EXCEPTION 'not allowed' USING ERRCODE = '42501';
  END IF;

  -- Check if product.launch_date is in the future.
  IF (SELECT launch_date > NOW() FROM public.products WHERE id = _product_id) THEN
    RETURN (SELECT votes_count FROM public.products WHERE id = _product_id);
  END IF;

  -- Check if the launch window has closed (QA-031).
  IF (SELECT launch_end < NOW() FROM public.products WHERE id = _product_id) THEN
    RAISE EXCEPTION 'voting closed' USING ERRCODE = '42501';
  END IF;

  -- If a vote by this user on this product exists, delete it; otherwise, add it.
  IF (SELECT EXISTS (SELECT 1 FROM public.product_votes WHERE product_id = _product_id AND user_id = _user_id)) THEN
    DELETE FROM public.product_votes WHERE product_id = _product_id AND user_id = _user_id;
  ELSE
    INSERT INTO public.product_votes(product_id, user_id) VALUES (_product_id, _user_id);
  END IF;

  -- Update the vote count for this product.
  UPDATE public.products SET votes_count = (SELECT COUNT(*) FROM public.product_votes WHERE product_id = _product_id) WHERE id = _product_id;

  -- Return the updated votes_count value.
  RETURN (SELECT votes_count FROM public.products WHERE id = _product_id);
END$function$;

-- Grants unchanged (authenticated + service_role only, see 20260926120000_secure_vote_rpcs.sql).
