<script>
  import { onMount } from 'svelte';
  import { connectJobStream } from './lib/sse.js';
  import { jobList, setJobs, upsertJob, removeJob } from './lib/store.js';
  import JobCard from './components/JobCard.svelte';

  onMount(() => {
    const stream = connectJobStream({ onResync: setJobs, onUpsert: upsertJob, onDelete: removeJob });
    return () => stream.close();
  });
</script>

<h1>Jacques</h1>

<div class="container-fluid">
  {#each $jobList as job (job.id)}
    <JobCard {job} />
  {/each}
</div>
