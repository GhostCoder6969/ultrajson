/*
 * Failing-allocator shim for OOM-path tests (issue #712).
 *
 * Routes PYMEM_DOMAIN_OBJ allocations through hooks which delegate every
 * call to the original allocator while:
 *   - failing (returning NULL for) allocations of exactly one configured
 *     size, so a test can OOM one malloc deterministically, and
 *   - tracking live blocks allocated while installed (allocs minus frees),
 *     so the test can assert the failing path cleaned up after itself.
 *
 * The hooks use no Python API and no interpreter allocator for their own
 * bookkeeping, so they cannot recurse into themselves.
 *
 * Test-only helper, not shipped. Build it next to the test suite with:
 *   gcc -shared -fPIC $(python3-config --includes) oomshim.c \
 *       -o oomshim$(python3-config --extension-suffix)
 */
#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <stddef.h>

static PyMemAllocatorEx saved;
static int installed = 0;
static size_t fail_size = 0;
static long fail_count = 0;

/* A single decode allocates a handful of objects; a fixed table keeps the
 * hooks free of any allocation of their own. */
#define MAX_LIVE 8192
static void *live[MAX_LIVE];
static size_t live_used = 0;

static void
track(void *p)
{
	if (p == NULL)
	{
		return;
	}
	for (size_t i = 0; i < live_used; i++)
	{
		if (live[i] == p)
		{
			return;
		}
	}
	if (live_used < MAX_LIVE)
	{
		live[live_used++] = p;
	}
}

static void
untrack(void *p)
{
	size_t i;

	if (p == NULL)
	{
		return;
	}
	for (i = 0; i < live_used; i++)
	{
		if (live[i] == p)
		{
			live[i] = live[--live_used];
			return;
		}
	}
	/* Pointer from before install: nothing to forget, just delegate. */
}

static void *
hook_malloc(void *ctx, size_t size)
{
	void *p;

	(void) ctx;
	if (fail_size != 0 && size == fail_size)
	{
		fail_count++;
		return NULL;
	}
	p = saved.malloc(saved.ctx, size);
	track(p);
	return p;
}

static void *
hook_calloc(void *ctx, size_t nelem, size_t elsize)
{
	void *p;
	size_t size = nelem * elsize;

	(void) ctx;
	if (fail_size != 0 && size == fail_size)
	{
		fail_count++;
		return NULL;
	}
	p = saved.calloc(saved.ctx, nelem, elsize);
	track(p);
	return p;
}

static void *
hook_realloc(void *ctx, void *ptr, size_t size)
{
	void *p;

	(void) ctx;
	if (fail_size != 0 && size == fail_size)
	{
		fail_count++;
		return NULL;
	}
	if (ptr != NULL)
	{
		untrack(ptr);
	}
	p = saved.realloc(saved.ctx, ptr, size);
	if (p == NULL)
	{
		/* Failed: the original block is still alive, unless the
		 * allocator freed it for a zero-size request. */
		if (ptr != NULL && size != 0)
		{
			track(ptr);
		}
		return NULL;
	}
	track(p);
	return p;
}

static void
hook_free(void *ctx, void *ptr)
{
	(void) ctx;
	untrack(ptr);
	saved.free(saved.ctx, ptr);
}

static PyMemAllocatorEx hooks = {
	NULL, hook_malloc, hook_calloc, hook_realloc, hook_free};

static PyObject *
oomshim_install(PyObject *self, PyObject *args)
{
	(void) self;
	if (!PyArg_ParseTuple(args, "n:install", &fail_size))
	{
		return NULL;
	}
	if (installed)
	{
		PyErr_SetString(PyExc_RuntimeError, "shim already installed");
		return NULL;
	}
	PyMem_GetAllocator(PYMEM_DOMAIN_OBJ, &saved);
	live_used = 0;
	fail_count = 0;
	PyMem_SetAllocator(PYMEM_DOMAIN_OBJ, &hooks);
	installed = 1;
	Py_RETURN_NONE;
}

static PyObject *
oomshim_uninstall(PyObject *self, PyObject *Py_UNUSED(ignored))
{
	(void) self;
	if (!installed)
	{
		PyErr_SetString(PyExc_RuntimeError, "shim not installed");
		return NULL;
	}
	PyMem_SetAllocator(PYMEM_DOMAIN_OBJ, &saved);
	installed = 0;
	fail_size = 0;
	Py_RETURN_NONE;
}

static PyObject *
oomshim_live(PyObject *self, PyObject *Py_UNUSED(ignored))
{
	(void) self;
	return PyLong_FromSize_t(live_used);
}

static PyObject *
oomshim_failures(PyObject *self, PyObject *Py_UNUSED(ignored))
{
	(void) self;
	return PyLong_FromLong(fail_count);
}

static PyMethodDef oomshim_methods[] = {
	{"install", oomshim_install, METH_VARARGS,
	 "install(fail_size): route OBJ allocations through the shim, "
	 "failing allocations of exactly fail_size bytes (0 disables failing)."},
	{"uninstall", oomshim_uninstall, METH_NOARGS,
	 "Restore the original OBJ allocator."},
	{"live", oomshim_live, METH_NOARGS,
	 "Number of blocks allocated through the shim and not yet freed."},
	{"failures", oomshim_failures, METH_NOARGS,
	 "Number of allocations the shim has failed since install."},
	{NULL, NULL, 0, NULL}};

static struct PyModuleDef oomshim_module = {
	PyModuleDef_HEAD_INIT, "oomshim",
	"Failing-allocator shim for OOM-path tests.", -1, oomshim_methods};

PyMODINIT_FUNC
PyInit_oomshim(void)
{
	return PyModule_Create(&oomshim_module);
}
