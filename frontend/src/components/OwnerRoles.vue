<script setup lang="ts">
import { computed } from 'vue'
import { ownerPresentation, ownerRoleLabel, ownerRoleTone } from '../domain'
import type { Project, User } from '../types'
const props = defineProps<{ project: Pick<Project, 'owner_assignments' | 'owner_roles' | 'owner_name'>; users: User[] }>()
const assignments = computed(() => ownerPresentation(props.project, props.users))
</script>
<template>
  <span class="owner-roles">
    <span v-for="(assignment, index) in assignments" :key="`${assignment.name}-${assignment.role}-${index}`" class="owner-role">
      <b v-if="assignment.role" :class="`role-${ownerRoleTone(assignment.role)}`">{{ ownerRoleLabel(assignment.role, assignment.primary) }}</b>
      {{ assignment.name }}
    </span>
  </span>
</template>
<style scoped>
.owner-roles{display:inline-flex;flex-wrap:wrap;gap:5px 8px;font-size:12px;font-weight:400;line-height:1.6}.owner-role{display:inline-flex;align-items:center;gap:4px;white-space:nowrap;color:#526176}.owner-role b{font-size:10px;font-weight:600;border-radius:4px;padding:1px 5px}.owner-role b.role-primary{color:#3068da;background:#eef3ff}.owner-role b.role-secondary{color:#7a54a6;background:#f3edfa}.owner-role b.role-neutral{color:#526176;background:#f2f4f7}
</style>
