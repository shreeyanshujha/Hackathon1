import React from 'react';
import { View } from 'react-native';
import { Field, TagEditor, StepperInput, InfoBanner } from '../../components/ui';
import { HEALTH_CONDITION_SUGGESTIONS } from '../../model/profile';

export default function HealthStep({ draft, setDraft }) {
  return (
    <View>
      <InfoBanner>
        Optional and self-reported. This is only used to give helpful context when we call
        your emergency contact (“they do have a heart condition — this may be relevant”).
        The wearable does not detect or diagnose any medical condition.
      </InfoBanner>

      <Field label="Conditions you're comfortable sharing (optional)">
        <TagEditor
          tags={draft.healthContext}
          onChange={(healthContext) => setDraft({ healthContext })}
          suggestions={HEALTH_CONDITION_SUGGESTIONS}
          placeholder="Add a condition…"
        />
      </Field>

      <Field
        label="Regular medications (optional)"
        hint="Just a count — this sets up medication reminders in a later version."
      >
        <StepperInput
          value={draft.medicationCount}
          onChange={(medicationCount) => setDraft({ medicationCount })}
        />
      </Field>
    </View>
  );
}
