// SDK-free C++ surface for COFF compile/symbol checks only. Never link this
// model into a runnable SDK build. Containers lower to CRT allocation; clock,
// mutex and thread operations lower to the same native Windows API families.
// This is deliberately local to the audio proof, not a replacement STL ABI.
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <stdarg.h>
extern "C" {
void *malloc(size_t);
void free(void *);
void *memcpy(void *, const void *, size_t);
void *memset(void *, int, size_t);
int memcmp(const void *, const void *, size_t);
int strcmp(const char *, const char *);
size_t strlen(const char *);
char *getenv(const char *);
int atoi(const char *);
double strtod(const char *, char **);
long strtol(const char *, char **, int);
int atexit(void (*)(void));
__declspec(noreturn) void exit(int);
typedef struct WinAudioFile FILE;
FILE *__acrt_iob_func(unsigned);
int fprintf(FILE *, const char *, ...);
int fputc(int, FILE *);
int snprintf(char *, size_t, const char *, ...);
int vsnprintf(char *, size_t, const char *, va_list);
double pow(double, double);
double sqrt(double);
double fabs(double);
long long llround(long double);
typedef struct { void *ptr; } AudioSrw;
__declspec(dllimport) void AcquireSRWLockExclusive(AudioSrw *);
__declspec(dllimport) void ReleaseSRWLockExclusive(AudioSrw *);
__declspec(dllimport) int TryAcquireSRWLockExclusive(AudioSrw *);
__declspec(dllimport) void Sleep(unsigned long);
__declspec(dllimport) int QueryPerformanceCounter(int64_t *);
__declspec(dllimport) int QueryPerformanceFrequency(int64_t *);
__declspec(dllimport) void *CreateThread(void *, size_t, unsigned long (*)(void *), void *, unsigned long, unsigned long *);
__declspec(dllimport) unsigned long WaitForSingleObject(void *, unsigned long);
__declspec(dllimport) int CloseHandle(void *);
}
#define stderr (__acrt_iob_func(2))
void *operator new(size_t);
void operator delete(void *) noexcept;
inline void *operator new(size_t, void *p) noexcept { return p; }
namespace std {
template<class T> class initializer_list {
    const T *p; size_t n;
public:
    const T *begin() const { return p; } const T *end() const { return p+n; }
};
template<class T> struct remove_reference { using type=T; };
template<class T> struct remove_reference<T &> { using type=T; };
template<class T> struct remove_reference<T &&> { using type=T; };
template<class T> typename remove_reference<T>::type &&move(T &&x) {
    return static_cast<typename remove_reference<T>::type &&>(x);
}
template<class T> const T &min(const T &a,const T &b) { return b<a?b:a; }
template<class T> const T &max(const T &a,const T &b) { return a<b?b:a; }
template<class T> void fill(T *a,T *b,const T &v) { while (a!=b) *a++=v; }
template<class T> T *remove(T *a,T *b,const T &v) {
    T *out=a;for (;a!=b;++a) if (!(*a==v)) *out++=std::move(*a);return out;
}
template<class T,class Compare> void stable_sort(T *a,T *b,Compare less) {
    if (a==b) return;
    for (T *p=a+1;p!=b;++p) { T v=*p;T *q=p;while(q!=a && less(v,q[-1])) { *q=q[-1];--q; } *q=v; }
}
template<class A,class B> struct pair { A first; B second; };
template<class T> class vector {
    T *p=nullptr; size_t n=0,capacity=0;
    void reserve(size_t c) {
        if (c<=capacity) return;
        T *q=(T *)malloc(c*sizeof(T));if (!q) exit(2);
        for (size_t i=0;i<n;++i) { new(q+i) T(std::move(p[i]));p[i].~T(); }
        free(p);p=q;capacity=c;
    }
public:
    vector()=default;
    vector(initializer_list<T> v) { assign(v.begin(),v.end()); }
    vector(size_t count,const T &v) { assign(count,v); }
    vector(const vector &v) { assign(v.begin(),v.end()); }
    vector(vector &&v) : p(v.p),n(v.n),capacity(v.capacity) { v.p=nullptr;v.n=v.capacity=0; }
    ~vector() { clear();free(p); }
    vector &operator=(const vector &v) { if (this!=&v) assign(v.begin(),v.end());return *this; }
    size_t size() const { return n; }
    bool empty() const { return !n; }
    T *begin() { return p; } const T *begin() const { return p; }
    T *end() { return n?p+n:p; } const T *end() const { return n?p+n:p; }
    T *data() { return p; } const T *data() const { return p; }
    T &operator[](size_t i) { return p[i]; } const T &operator[](size_t i) const { return p[i]; }
    T &front() { return p[0]; } T &back() { return p[n-1]; }
    void clear() { while(n) p[--n].~T(); }
    void resize(size_t count) {
        reserve(count);
        while(n>count) p[--n].~T();
        while(n<count) { new(p+n) T{};++n; }
    }
    void assign(size_t count,const T &v) { clear();reserve(count);while(n<count) { new(p+n) T(v);++n; } }
    void assign(const T *a,const T *b) { clear();size_t count=a==b?0:(size_t)(b-a);reserve(count);while(a!=b) { new(p+n) T(*a++);++n; } }
    void push_back(const T &v) { if(n==capacity) reserve(capacity?capacity*2:4);new(p+n) T(v);++n; }
    T *erase(T *a,T *b) {
        size_t first=(size_t)(a-p),count=(size_t)(b-a);
        for(size_t i=first;i+count<n;++i) p[i]=std::move(p[i+count]);
        for(size_t i=0;i<count;++i) p[--n].~T();return p+first;
    }
    T *erase(T *a) { return erase(a,a+1); }
};
class string {
    vector<char> text;
public:
    string() { text.push_back(0); }
    string(const char *s) { size_t n=strlen(s);text.assign(s,s+n+1); }
    string(const string &)=default;
    string &operator=(const string &)=default;
    string &operator=(const char *s) { *this=string(s);return *this; }
    bool empty() const { return text.size()==1; }
    void clear() { text.assign(1,0); }
    const char *c_str() const { return text.data(); }
    bool operator==(const char *s) const { return !strcmp(c_str(),s); }
    string &operator+=(const char *s) {
        size_t old=text.size()-1,n=strlen(s);text.resize(old+n+1);memcpy(text.data()+old,s,n+1);return *this;
    }
    string &operator+=(const string &s) { return *this+=s.c_str(); }
};
inline string operator+(string a,const string &b) { return a+=b; }
inline string operator+(string a,const char *b) { return a+=b; }
inline string operator+(const char *a,const string &b) { return string(a)+=b; }
inline string to_string(int n) { char b[32];snprintf(b,sizeof b,"%d",n);return string(b); }
inline double pow(double a,double b) { return ::pow(a,b); }
inline double sqrt(double a) { return ::sqrt(a); }
inline float fabs(float a) { return (float)::fabs(a); }
inline double fabs(double a) { return ::fabs(a); }
inline long long llround(long double a) { return ::llround(a); }
enum memory_order { memory_order_relaxed, memory_order_acquire, memory_order_release, memory_order_acq_rel };
template<class T> class atomic {
    T value;
public:
    constexpr atomic(T v) : value(v) {}
    T load(memory_order=memory_order_acquire) const { return __atomic_load_n(&value,__ATOMIC_SEQ_CST); }
    void store(T v,memory_order=memory_order_release) { __atomic_store_n(&value,v,__ATOMIC_SEQ_CST); }
    T exchange(T v,memory_order=memory_order_acq_rel) { return __atomic_exchange_n(&value,v,__ATOMIC_SEQ_CST); }
    bool compare_exchange_strong(T &expected,T desired,memory_order=memory_order_acq_rel) {
        return __atomic_compare_exchange_n(&value,&expected,desired,false,__ATOMIC_SEQ_CST,__ATOMIC_SEQ_CST);
    }
    T fetch_and(T v,memory_order=memory_order_acq_rel) { return __atomic_fetch_and(&value,v,__ATOMIC_SEQ_CST); }
    T operator++() { return __atomic_add_fetch(&value,1,__ATOMIC_SEQ_CST); }
    T operator=(T v) { store(v);return v; }
    operator T() const { return load(); }
};
class mutex {
    AudioSrw lock_{};
public:
    void lock() { AcquireSRWLockExclusive(&lock_); }
    void unlock() { ReleaseSRWLockExclusive(&lock_); }
    bool try_lock() { return TryAcquireSRWLockExclusive(&lock_)!=0; }
};
template<class T> class lock_guard {
    T &m;
public:
    explicit lock_guard(T &v):m(v) { m.lock(); } ~lock_guard() { m.unlock(); }
};
template<class T> class unique_ptr {
    T *p=nullptr;
public:
    unique_ptr()=default;
    unique_ptr(decltype(nullptr)) {}
    explicit unique_ptr(T *v):p(v) {}
    unique_ptr(unique_ptr &&v):p(v.release()) {}
    ~unique_ptr() { delete p; }
    unique_ptr &operator=(unique_ptr &&v) { if(this!=&v) reset(v.release());return *this; }
    unique_ptr &operator=(decltype(nullptr)) { reset();return *this; }
    T *get() const { return p; } T *operator->() const { return p; }
    T &operator*() const { return *p; } explicit operator bool() const { return p!=nullptr; }
    T *release() { T *v=p;p=nullptr;return v; }
    void reset(T *v=nullptr) { delete p;p=v; }
};
template<class T> unique_ptr<T> make_unique() { return unique_ptr<T>(new T); }
namespace chrono {
// Time is represented as nanoseconds for this compile-only clock model.
template<class Rep, int64_t Scale=1000000000> struct duration {
    long double ns=0;
    duration()=default;
    explicit duration(Rep v):ns((long double)v*Scale) {}
    template<class R,int64_t S> duration(duration<R,S> v):ns(v.ns) {}
    Rep count() const { return (Rep)(ns/Scale); }
};
using nanoseconds=duration<int64_t,1>;
using microseconds=duration<int64_t,1000>;
using milliseconds=duration<int64_t,1000000>;
using seconds=duration<int64_t,1000000000>;
template<class D,class R,int64_t S> D duration_cast(duration<R,S> v) { return D(v); }
template<class A,int64_t S,class B,int64_t T> bool operator<(duration<A,S> a,duration<B,T> b) { return a.ns<b.ns; }
template<class A,int64_t S,class B,int64_t T> bool operator>(duration<A,S> a,duration<B,T> b) { return a.ns>b.ns; }
template<class A,int64_t S,class B,int64_t T> bool operator>=(duration<A,S> a,duration<B,T> b) { return a.ns>=b.ns; }
template<class A,int64_t S,class B,int64_t T> nanoseconds operator+(duration<A,S> a,duration<B,T> b) { return nanoseconds((int64_t)(a.ns+b.ns)); }
template<class A,int64_t S,class B,int64_t T> nanoseconds operator-(duration<A,S> a,duration<B,T> b) { return nanoseconds((int64_t)(a.ns-b.ns)); }
inline nanoseconds operator/(nanoseconds a,int n) { return nanoseconds(a.count()/n); }
struct steady_clock {
    using duration=nanoseconds;
    struct time_point {
        int64_t ns=0;
        duration time_since_epoch() const { return duration(ns); }
        time_point &operator+=(duration d) { ns+=d.count();return *this; }
    };
    static time_point now() { int64_t n=0,f=1;QueryPerformanceCounter(&n);QueryPerformanceFrequency(&f);return {(int64_t)((long double)n*1e9L/f)}; }
};
inline nanoseconds operator-(steady_clock::time_point a,steady_clock::time_point b) { return nanoseconds(a.ns-b.ns); }
template<class R,int64_t S> steady_clock::time_point operator+(steady_clock::time_point a,duration<R,S> b) { return {a.ns+(int64_t)b.ns}; }
template<class R,int64_t S> steady_clock::time_point operator-(steady_clock::time_point a,duration<R,S> b) { return {a.ns-(int64_t)b.ns}; }
inline bool operator>=(steady_clock::time_point a,steady_clock::time_point b) { return a.ns>=b.ns; }
inline bool operator<(steady_clock::time_point a,steady_clock::time_point b) { return a.ns<b.ns; }
}
namespace this_thread {
inline void yield() { Sleep(0); }
template<class R,int64_t S> void sleep_for(chrono::duration<R,S> d) { Sleep((unsigned long)(d.ns/1000000)); }
inline void sleep_until(chrono::steady_clock::time_point t) { auto d=t-chrono::steady_clock::now();if(d.count()>0) sleep_for(d); }
}
class thread {
    void *handle=nullptr;
    struct Task { virtual void call()=0; virtual ~Task()=default; };
    template<class F> struct Closure:Task {
        F fn; explicit Closure(F f):fn(std::move(f)) {} void call() override { fn(); }
    };
    static unsigned long enter(void *p) { auto *t=(Task *)p;t->call();delete t;return 0; }
public:
    thread()=default;
    template<class F> explicit thread(F f) { handle=CreateThread(nullptr,0,enter,new Closure<F>(std::move(f)),0,nullptr); }
    template<class F,class P> thread(F f,P p):thread([f,p] { f(p); }) {}
    thread(thread &&t):handle(t.handle) { t.handle=nullptr; }
    thread &operator=(thread &&t) { handle=t.handle;t.handle=nullptr;return *this; }
    bool joinable() const { return handle!=nullptr; }
    void join() { WaitForSingleObject(handle,0xffffffffu);CloseHandle(handle);handle=nullptr; }
    void detach() { CloseHandle(handle);handle=nullptr; }
};
}
