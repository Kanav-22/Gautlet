# Open Questions

The implementation model must explicitly resolve these before or during architecture work.

## Architecture

1. Should run indexing use SQLite in the MVP or only files?
2. Which subsystem owns environment fingerprinting?
3. How should adapter processes communicate with the core?
4. Is Docker support part of MVP or first post-MVP release?

## Evaluation

5. How should judge-based evaluation confidence be calculated?
6. What minimum repeats are required for reproducibility claims?
7. How should non-deterministic systems be compared fairly?
8. Which metrics are universal versus benchmark-specific?

## Scoring

9. Should overall scoring be shown when evidence is incomplete?
10. How should severe security failures cap scores?
11. Should benchmark packs control weights or only recommend them?

## Security

12. Can a local subprocess sandbox provide acceptable protection?
13. How are hidden benchmark fixtures protected from the system under test?
14. How should plugin permissions be represented?

## Product

15. Should LangGraph be a first-party plugin or example integration?
16. What is the smallest compelling demo?
17. Which existing project should be the first real evaluation target?

## Future

18. What would be required for signed reports?
19. How can community benchmark packs be trusted?
20. How can benchmark leakage and score gaming be reduced?
