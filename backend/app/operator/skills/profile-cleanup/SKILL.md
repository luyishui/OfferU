# Profile Cleanup Skill

## Purpose
Clean and consolidate actor-owned profile facts without changing their meaning.

## Required sequence
1. Activate the Skill and load the target Profile and ProfileSection schema.
2. Read the relevant sections and identify duplicates, formatting issues, and conflicts.
3. Show the proposed normalized facts and ask for confirmation when meaning changes.
4. Use only the registered profile cleanup action or proposal-gated profile patch.

## Safety boundaries
- Do not delete facts merely because they are uncommon or incomplete.
- Do not convert suggestions into official facts.
- Keep actor/session scope and version fencing intact.
