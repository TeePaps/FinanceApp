# End Requirements Gathering

Complete or cancel the current requirements session.

## Instructions:

1. Read requirements/.current-requirement
2. If no active requirement:
   - Show "No active requirement to end"
   - Exit

3. Show current phase progress
4. Present options:
   a. Generate spec with current answers (fill gaps with assumptions)
   b. Mark as incomplete (save progress for later)
   c. Cancel and delete

## Option A: Generate Spec
- Create 06-requirements-spec.md with all collected answers
- Use smart defaults for unanswered questions
- Mark clearly which items are assumptions
- Include implementation guidance
- Mark requirement as complete

## Option B: Mark Incomplete
- Update metadata status to "incomplete"
- Add timestamp for when paused
- Summarize what was gathered
- Note what remains to be done

## Option C: Cancel
- Confirm with user
- Remove requirement folder
- Clear .current-requirement

## Final Output Format:
```markdown
# Requirements Specification: [Feature Name]
Generated: [timestamp]

## Overview
[Brief description of the feature]

## Functional Requirements
### Must Have
- [Requirement based on YES answers]
- [Requirement based on YES answers]

### Should Have
- [Requirement based on defaults/assumptions]

## Technical Requirements
- Files to modify: [specific paths]
- New files needed: [if any]
- Database changes: [if any]
- API endpoints: [if any]

## Assumptions (Unconfirmed)
- [Items where defaults were used]

## Implementation Notes
- Follow patterns from: [similar features]
- Use existing: [services/utilities]
- Consider: [constraints discovered]

## Acceptance Criteria
- [ ] [Testable criterion]
- [ ] [Testable criterion]
```

## After Completion:
- Clear requirements/.current-requirement
- Update requirements/index.md with summary
